"""Watch mode glue (SPEC §18): watched folders -> settle -> watch session pipeline -> auto-move
or pending queue, plus notifications and the activity digest.

Model: every (watched folder, output tree) pair owns one long-lived *watch session*
(Session.kind == "watch"), created once and reused across daemon restarts. New arrivals are
added to it incrementally; its READY cards are the pending queue the user clears with the
normal swipe UI. Automatic moves are ordinary journaled decisions (Decision.source == "auto"),
so both session undo and the targeted per-item undo work through the executor.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

from sqlmodel import Session as DB
from sqlmodel import col, select

from swipesort.api.schemas import (
    DecideIn,
    StreamEvent,
    WatchActivity,
    WatchActivityItem,
    WatchConfigIn,
    WatchConfigOut,
    WatchFolderOut,
    WatchSafety,
    WatchStatus,
)
from swipesort.api.service import BadRequest, NotFound, SessionService
from swipesort.config import Watch, WatchFolder, app_dir, watch_overlay_path
from swipesort.db.models import Decision, File, FileStatus, Folder, Op, OpStatus, OpType, Suggestion
from swipesort.db.models import Session as SessionRow
from swipesort.exec.errors import ExecError, LockedFileError
from swipesort.pipeline.scanner import scan_entry
from swipesort.platform.base import Platform
from swipesort.watch import policy
from swipesort.watch.settle import Settler, is_ignored
from swipesort.watch.watcher import FolderWatcher, unit_for

log = logging.getLogger(__name__)

MAX_LOCKED_RETRIES = 5


@dataclass
class _Folder:
    cfg: WatchFolder
    root: Path
    output: Path
    sid: int | None = None
    watcher: FolderWatcher | None = None
    error: str = ""
    since: float = 0.0  # wall time the watch session was created (catch-up / rescan cutoff)
    excludes: list[Path] = field(default_factory=list)


class WatchService:
    def __init__(self, sessions: SessionService, platform: Platform | None = None,
                 clock: Callable[[], float] = time.monotonic, home: Path | None = None) -> None:
        self.sessions = sessions
        self.settings = sessions.settings
        self.platform = platform or sessions.platform
        self.home = home
        self._clock = clock
        self._lock = threading.RLock()
        self._settler = Settler(self.cfg.settle_seconds, self.platform.is_locked, clock)
        self._owner: dict[Path, _Folder] = {}
        self.folders: list[_Folder] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_rescan = 0.0
        self.running = False
        # wall-clock time each unit settled and was handed to the pipeline (observability)
        self.settled_at: dict[str, float] = {}
        # auto actions that hit a locked file (e.g. an antivirus scan): (sid, fid) -> (due, n)
        self._retry_due: dict[tuple[int, int], float] = {}
        self._retry_n: dict[tuple[int, int], int] = {}

    @property
    def cfg(self) -> Watch:
        return self.settings.watch

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._settler = Settler(self.cfg.settle_seconds, self.platform.is_locked,
                                    self._clock)
            self._owner.clear()
            self.folders = []
            if self.cfg.enabled:
                for wf in self.cfg.resolved_folders():
                    if wf.enabled:
                        self.folders.append(self._open(wf))
            self.running = True
            self._thread = threading.Thread(target=self._loop, name="watch", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            for fo in self.folders:
                if fo.watcher is not None:
                    fo.watcher.stop()
                    fo.watcher = None
            self.running = False
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=5)
        self._thread = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def _open(self, wf: WatchFolder) -> _Folder:
        root = Path(wf.path).expanduser()
        output = Path(wf.output).expanduser()
        fo = _Folder(cfg=wf, root=root, output=output)
        try:
            fo.sid = self.sessions.open_watch_session(str(root), str(output), on_scored=None)
            fo.root, fo.output = root.resolve(), output.resolve()
            pipe = self.sessions.pipelines[fo.sid]
            pipe.on_scored = partial(self._on_scored, fo.sid)
            with DB(self.sessions.engine) as db:
                row = db.get(SessionRow, fo.sid)
                created = row.created_at if row is not None else datetime.now(UTC)
            fo.since = (created if created.tzinfo else created.replace(tzinfo=UTC)).timestamp()
            if fo.output.is_relative_to(fo.root):
                fo.excludes = [fo.output]  # output nested in the watched folder: not "new"
            fo.watcher = FolderWatcher(fo.root, partial(self._on_path, fo))
            fo.watcher.start()
            self._scan_top(fo)  # catch up on what arrived while the daemon was down
        except (BadRequest, NotFound, OSError) as e:
            fo.error = str(e)
            log.warning("cannot watch %s: %s", wf.path, e)
        return fo

    # ------------------------------------------------------------------ events -> settle
    def _on_path(self, fo: _Folder, path: Path) -> None:
        unit = unit_for(path, fo.root, self.settings.scan, self.platform, self.cfg.ignore,
                        fo.excludes)
        if unit is None:
            return
        with self._lock:
            self._owner[unit] = fo
            self._settler.touch(unit)

    def _scan_top(self, fo: _Folder) -> None:
        """Track top-level entries that are new since the watch session began and unknown to
        it (missed events, or arrivals while the daemon was down)."""
        if fo.sid is None:
            return
        known = self.sessions.known_paths(fo.sid)
        try:
            entries = list(fo.root.iterdir())
        except OSError:
            return
        for e in entries:
            if str(e) in known or e in self._settler:
                continue
            try:
                st = e.lstat()
            except OSError:
                continue
            # ctime: inode change (macOS: also set by a move into the folder) / creation
            # (Windows), so a copied file with an old mtime still counts as new
            if max(st.st_mtime, st.st_ctime) < fo.since:
                continue
            self._on_path(fo, e)

    def _loop(self) -> None:
        while not self._stop.wait(max(0.05, self.cfg.settle_poll_seconds)):
            try:
                self.tick()
            except Exception:
                log.exception("watch loop step failed")

    def tick(self) -> list[int]:
        """One settle poll (+ periodic rescan). Returns the File ids added."""
        with self._lock:
            settled = self._settler.poll()
            owners = [(p, self._owner.pop(p, None)) for p in settled]
        added: list[int] = []
        for unit, fo in owners:
            if fo is not None and fo.sid is not None:
                added += self._add_unit(fo, unit)
        now = self._clock()
        with self._lock:
            due = [k for k, t in self._retry_due.items() if t <= now]
            for k in due:
                del self._retry_due[k]
        for sid, fid in due:
            self._on_scored(sid, [fid])
        if self.cfg.rescan_seconds > 0 and now - self._last_rescan >= self.cfg.rescan_seconds:
            self._last_rescan = now
            for fo in list(self.folders):
                self._scan_top(fo)
        return added

    def _add_unit(self, fo: _Folder, unit: Path) -> list[int]:
        assert fo.sid is not None
        if not unit.exists():
            return []
        known = self.sessions.known_paths(fo.sid)
        items = [it for it in scan_entry(unit, fo.root, self.settings.scan, self.platform)
                 if str(it.path) not in known
                 and not is_ignored(it.rel_path.split("/"), self.cfg.ignore)]
        if not items:
            return []
        ids = self.sessions.add_files(fo.sid, items)
        now = time.time()
        for it in items:
            self.settled_at[str(it.path)] = now
        return ids

    # ------------------------------------------------------------------ scored -> act
    def _on_scored(self, sid: int, ids: list[int]) -> None:
        """Pipeline thread: decide auto-move / quarantine / pending for freshly READY files."""
        decisions = self.sessions.user_decision_count()
        sorted_msgs: list[tuple[str, int]] = []
        for fid in ids:
            with DB(self.sessions.engine) as db:
                f = db.get(File, fid)
                if f is None or f.status != FileStatus.ready:
                    continue
                sug = db.exec(select(Suggestion).where(
                    Suggestion.file_id == fid, Suggestion.kind == "classify")
                    .order_by(col(Suggestion.id).desc())).first()
                dup = self.sessions._dup_suggestion(db, fid)
                relation: str | None = None
                in_output = False
                if dup is not None:
                    other = json.loads(dup.candidates_json)
                    relation = str(other.get("relation"))
                    in_output = str(other.get("other", "")).startswith("ix:")
                target: int | None = None
                if sug is not None:
                    cands = json.loads(sug.candidates_json)
                    target = int(cands[0]["folder_id"]) if cands else None
                verdict = policy.decide(
                    self.cfg, decisions=decisions, tier=sug.tier if sug else None,
                    confidence=sug.confidence if sug else 0.0, dup_relation=relation,
                    dup_in_output=in_output)
                name = Path(f.path).name
                row = db.get(SessionRow, sid)
                out_root = Path(row.output_root) if row else Path()
                tgt = db.get(Folder, target) if target is not None else None
            if verdict.action == "pending" or (verdict.action == "move" and tgt is None):
                log.info("watch: %s pending (%s)", name, verdict.why)
                continue
            try:
                if verdict.action == "move":
                    assert tgt is not None
                    res = self.sessions.decide(
                        f"m{fid}", DecideIn(action="pick", target_folder_id=tgt.id),
                        source="auto")
                    rel = Path(tgt.path).relative_to(out_root).as_posix() if tgt else ""
                    msg = f"Sorted {name} → {rel or '(output root)'}"
                else:
                    res = self.sessions.decide(f"m{fid}", DecideIn(action="trash"),
                                               source="auto")
                    msg = f"Quarantined duplicate {name}"
            except LockedFileError as e:
                with self._lock:  # retry a few times after a settle period, then leave it
                    n = self._retry_n.get((sid, fid), 0) + 1
                    self._retry_n[(sid, fid)] = n
                    if n <= MAX_LOCKED_RETRIES:
                        self._retry_due[(sid, fid)] = self._clock() + self.cfg.settle_seconds
                log.info("watch: %s is locked (%s), attempt %d", name, e, n)
                continue  # stays a pending card meanwhile; never forced
            except (ExecError, BadRequest, NotFound) as e:
                log.info("watch: auto %s of %s skipped: %s", verdict.action, name, e)
                continue
            log.info("watch: %s (%s)", msg, verdict.why)
            sorted_msgs.append((msg, res.decision_id))
            self.sessions.events.publish(sid, StreamEvent(
                type="auto_sorted", message=msg, decision_id=res.decision_id))
        if sorted_msgs:
            self._notify(sorted_msgs)

    def undo_url(self, decision_id: int | None = None) -> str:
        port = self.settings.server.port
        q = f"?undo={decision_id}" if decision_id is not None else ""
        return f"http://127.0.0.1:{port}/watch{q}"

    def _notify(self, msgs: list[tuple[str, int]]) -> None:
        if not self.cfg.notify:
            return
        if len(msgs) > self.cfg.notify_summary_over:
            args = ("SwipeSort", f"Sorted {len(msgs)} new files automatically", self.undo_url())
        else:
            args = ("SwipeSort", msgs[0][0] if len(msgs) == 1
                    else "; ".join(m for m, _ in msgs), self.undo_url(msgs[-1][1]))

        def run() -> None:
            try:
                self.platform.notify(*args)
            except Exception:  # notifications are best effort, never fatal
                log.debug("notify failed", exc_info=True)

        threading.Thread(target=run, name="watch-notify", daemon=True).start()

    # ------------------------------------------------------------------ API views
    def _safety(self) -> WatchSafety:
        n = self.sessions.user_decision_count()
        return WatchSafety(decisions=n, min_decisions_for_auto=self.cfg.min_decisions_for_auto,
                           auto_unlocked=policy.auto_unlocked(self.cfg, n))

    def _folder_views(self) -> list[WatchFolderOut]:
        live = {(str(Path(f.cfg.path).expanduser()), str(Path(f.cfg.output).expanduser())): f
                for f in self.folders}
        out: list[WatchFolderOut] = []
        with DB(self.sessions.engine) as db:
            for wf in self.cfg.resolved_folders():
                fo = live.get((str(Path(wf.path).expanduser()), str(Path(wf.output).expanduser())))
                sid = fo.sid if fo else self._find_session(db, wf)
                pending = processing = 0
                if sid is not None:
                    for st in db.exec(select(File.status).where(File.session_id == sid)).all():
                        if st in (FileStatus.ready, FileStatus.skipped):
                            pending += 1
                        elif st == FileStatus.queued:
                            processing += 1
                out.append(WatchFolderOut(
                    path=wf.path, output=wf.output, enabled=wf.enabled, session_id=sid,
                    running=bool(fo and fo.watcher and fo.watcher.alive),
                    pending=pending, processing=processing, error=fo.error if fo else ""))
        return out

    @staticmethod
    def _find_session(db: DB, wf: WatchFolder) -> int | None:
        try:
            inp = str(Path(wf.path).expanduser().resolve())
            out = str(Path(wf.output).expanduser().resolve())
        except OSError:
            return None
        row = db.exec(select(SessionRow).where(
            SessionRow.kind == "watch", SessionRow.input_root == inp,
            SessionRow.output_root == out)).first()
        return row.id if row is not None else None

    def config(self) -> WatchConfigOut:
        c = self.cfg
        return WatchConfigOut(
            enabled=c.enabled, running=self.running and any(f.watcher for f in self.folders),
            folders=self._folder_views(), settle_seconds=c.settle_seconds,
            auto_move_threshold=c.auto_move_threshold, auto_move=c.auto_move, notify=c.notify,
            auto_trash_exact_dupes=c.auto_trash_exact_dupes, ignore=c.ignore,
            digest_days=c.digest_days, safety=self._safety())

    def status(self) -> WatchStatus:
        folders = self._folder_views()
        return WatchStatus(enabled=self.cfg.enabled,
                           running=self.running and any(f.watcher for f in self.folders),
                           pending_total=sum(f.pending for f in folders), folders=folders,
                           safety=self._safety())

    def update(self, body: WatchConfigIn) -> WatchConfigOut:
        c = self.cfg
        if body.folders is not None:
            for f in body.folders:
                for p in (f.path, f.output):
                    q = Path(p).expanduser()
                    if not q.is_absolute() or not q.is_dir() or q.is_symlink():
                        raise BadRequest(f"not a directory: {p}")
                if Path(f.path).expanduser().resolve() == Path(f.output).expanduser().resolve():
                    raise BadRequest("a watched folder can't be its own output tree")
            c.folders = [WatchFolder(path=f.path, output=f.output, enabled=f.enabled)
                         for f in body.folders]
        for name in ("enabled", "auto_move", "notify", "auto_trash_exact_dupes"):
            v = getattr(body, name)
            if v is not None:
                setattr(c, name, v)
        self._persist()
        self.restart()
        return self.config()

    def _persist(self) -> None:
        """Save the app-editable watch settings over config.toml (a config file, not user
        data: no executor involved)."""
        c = self.cfg
        data = {"enabled": c.enabled, "auto_move": c.auto_move, "notify": c.notify,
                "auto_trash_exact_dupes": c.auto_trash_exact_dupes,
                "folders": [f.model_dump() for f in c.folders]}
        path = watch_overlay_path(self.home or app_dir())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            log.warning("could not save watch settings: %s", e)

    def activity(self, days: int | None = None) -> WatchActivity:
        days = self.cfg.digest_days if days is None else max(1, days)
        since = datetime.now(UTC) - timedelta(days=days)
        items: list[WatchActivityItem] = []
        with DB(self.sessions.engine) as db:
            decs = db.exec(select(Decision).where(
                Decision.source == "auto", col(Decision.ts) >= since)
                .order_by(col(Decision.id).desc())).all()
            roots: dict[int, Path] = {}
            for d in decs:
                assert d.id is not None
                ops = db.exec(select(Op).where(
                    Op.decision_id == d.id,
                    col(Op.type).in_((OpType.move, OpType.trash)))).all()
                op = next((o for o in ops if o.status in (OpStatus.done, OpStatus.undone)), None)
                if op is None:
                    continue
                if d.session_id not in roots:
                    row = db.get(SessionRow, d.session_id)
                    roots[d.session_id] = Path(row.output_root) if row else Path()
                dst = Path(op.dst)
                try:
                    rel = dst.parent.relative_to(roots[d.session_id]).as_posix()
                except ValueError:
                    rel = ""
                conf: float | None = None
                if d.file_id is not None:
                    sg = db.exec(select(Suggestion).where(
                        Suggestion.file_id == d.file_id, Suggestion.kind == "classify")
                        .order_by(col(Suggestion.id).desc())).first()
                    conf = sg.confidence if sg is not None else None
                ts = d.ts if d.ts.tzinfo else d.ts.replace(tzinfo=UTC)
                items.append(WatchActivityItem(
                    decision_id=d.id, session_id=d.session_id, file_id=d.file_id,
                    name=Path(op.src).name, action="trash" if op.type == OpType.trash else "move",
                    src=op.src, dst=op.dst, dest_rel="" if rel == "." else rel,
                    confidence=conf, ts=ts, undone=d.undone, can_undo=False))
        for it in items:
            if not it.undone:
                why = self.sessions.undo_conflict(it.decision_id)
                it.can_undo = why is None
                it.undo_blocked = why or ""
        pending = self.status().pending_total
        return WatchActivity(
            days=days, items=items, pending_total=pending,
            auto_moved=sum(1 for i in items if i.action == "move" and not i.undone),
            auto_trashed=sum(1 for i in items if i.action == "trash" and not i.undone))
