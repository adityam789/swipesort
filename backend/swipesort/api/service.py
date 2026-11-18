"""Session service: glue between DB, scanner and executor. Routes stay thin."""

from __future__ import annotations

import json
import threading
from pathlib import Path, PurePosixPath

from sqlalchemy.engine import Engine
from sqlmodel import Session as DB
from sqlmodel import col, select

from swipesort.api.schemas import (
    Candidate,
    Card,
    DecideIn,
    DecideOut,
    Edge,
    FileInfo,
    FolderOut,
    Progress,
    SessionOut,
    TreeNode,
    UndoOut,
)
from swipesort.config import Settings
from swipesort.db.models import (
    Decision,
    File,
    FileStatus,
    Folder,
    IndexedFile,
    Op,
    OpStatus,
    OpType,
    Suggestion,
)
from swipesort.db.models import Session as SessionRow
from swipesort.exec.executor import Executor
from swipesort.exec.names import sanitize_name
from swipesort.ml import get_provider
from swipesort.ml.gateway import Provider
from swipesort.pipeline.scanner import list_dirs, scan
from swipesort.pipeline.worker import SessionPipeline
from swipesort.platform import get_platform
from swipesort.platform.base import Platform

REMAINING = (FileStatus.queued, FileStatus.ready, FileStatus.skipped)
SHOWABLE = (FileStatus.ready, FileStatus.skipped)
INBOX_NAME = "_Inbox"


class NotFound(Exception):
    pass


class BadRequest(Exception):
    pass


def _rel(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return "" if rel == "." else rel


class SessionService:
    def __init__(self, engine: Engine, settings: Settings, platform: Platform | None = None,
                 provider: Provider | None = None):
        self.engine = engine
        self.settings = settings
        self.platform = platform or get_platform()
        self.provider = provider or get_provider(settings)
        self.pipelines: dict[int, SessionPipeline] = {}
        self._executors: dict[int, Executor] = {}
        self._locks: dict[int, threading.Lock] = {}
        self._last_target: dict[int, int] = {}

    # ---------- helpers ----------
    def _session(self, db: DB, sid: int) -> SessionRow:
        row = db.get(SessionRow, sid)
        if row is None:
            raise NotFound(f"session {sid}")
        return row

    def _executor(self, row: SessionRow) -> Executor:
        assert row.id is not None
        ex = self._executors.get(row.id)
        if ex is None:
            ex = Executor(
                self.engine, row.id, Path(row.input_root), Path(row.output_root),
                platform=self.platform, settings=self.settings,
            )
            ex.recover()
            self._executors[row.id] = ex
        return ex

    def _lock(self, sid: int) -> threading.Lock:
        return self._locks.setdefault(sid, threading.Lock())

    def _progress(self, db: DB, sid: int) -> Progress:
        files = db.exec(select(File).where(File.session_id == sid)).all()
        remaining = sum(1 for f in files if f.status in REMAINING)
        decided = sum(1 for f in files if f.status == FileStatus.decided)
        ready = sum(1 for f in files if f.status in SHOWABLE)
        processed = sum(1 for f in files if f.status != FileStatus.queued)
        return Progress(scanned=len(files), processed=processed, ready=ready, decided=decided,
                        remaining=remaining)

    def _session_out(self, db: DB, row: SessionRow) -> SessionOut:
        assert row.id is not None
        return SessionOut(
            id=row.id, input_root=row.input_root, output_root=row.output_root,
            status=row.status, progress=self._progress(db, row.id),
        )

    def _add_folder(self, db: DB, sid: int, path: Path, root: Path, created_by: str) -> Folder:
        rel = _rel(path, root)
        parent: Folder | None = None
        if rel:
            parent_path = path.parent
            parent = db.exec(
                select(Folder).where(Folder.session_id == sid, Folder.path == str(parent_path))
            ).first()
        f = Folder(
            session_id=sid, path=str(path), parent_id=parent.id if parent else None,
            depth=len(PurePosixPath(rel).parts) if rel else 0, created_by=created_by,
            is_inbox=path.name == INBOX_NAME,
        )
        if parent is not None and parent.is_leaf:
            parent.is_leaf = False
            db.add(parent)
        db.add(f)
        db.flush()
        return f

    # ---------- sessions ----------
    def create_session(self, input_root: str, output_root: str) -> SessionOut:
        inp, out = Path(input_root).expanduser(), Path(output_root).expanduser()
        for p in (inp, out):
            if not p.is_absolute():
                raise BadRequest(f"path must be absolute: {p}")
            if p.is_symlink() or not p.is_dir():
                raise BadRequest(f"not a directory: {p}")
            rep = self.platform.check_access(p)
            if not rep.ok:
                raise BadRequest(rep.message or f"cannot access {p}")
        inp, out = inp.resolve(), out.resolve()
        with DB(self.engine) as db:
            row = SessionRow(input_root=str(inp), output_root=str(out))
            db.add(row)
            db.flush()
            assert row.id is not None
            sid = row.id
            # Output folder tree (sort-in-place: input files inside output are still input items).
            self._add_folder(db, sid, out, out, "existing")
            for d in list_dirs(out, self.settings.scan, self.platform):
                self._add_folder(db, sid, d, out, "existing")
            # Input files. When output is nested in input, skip the output subtree.
            for item in scan(inp, self.settings.scan, self.platform):
                if out != inp and out.is_relative_to(inp) and item.path.is_relative_to(out):
                    continue
                db.add(File(
                    session_id=sid, path=str(item.path), rel_path=item.rel_path,
                    is_bundle=item.is_bundle, size=item.size, mtime=item.mtime, ext=item.ext,
                    status=FileStatus.queued,
                ))
            db.commit()
            db.refresh(row)
            out_model = self._session_out(db, row)
        pipe = SessionPipeline(self.engine, self.settings, self.platform, self.provider, sid)
        self.pipelines[sid] = pipe
        pipe.start()
        return out_model

    def get_session(self, sid: int) -> SessionOut:
        with DB(self.engine) as db:
            return self._session_out(db, self._session(db, sid))

    # ---------- folders ----------
    def list_folders(self, sid: int) -> list[FolderOut]:
        with DB(self.engine) as db:
            row = self._session(db, sid)
            root = Path(row.output_root)
            folders = db.exec(
                select(Folder).where(Folder.session_id == sid, col(Folder.is_inbox).is_(False))
            ).all()
            out = []
            for f in folders:
                assert f.id is not None
                rel = _rel(Path(f.path), root)
                out.append(FolderOut(
                    id=f.id, path=rel, name=Path(f.path).name if rel else "(output root)",
                    depth=f.depth, created_by=f.created_by,
                ))
            return sorted(out, key=lambda x: x.path.casefold())

    def create_folder(self, sid: int, parent_id: int | None, name: str) -> FolderOut:
        clean = sanitize_name(name.strip())
        with self._lock(sid), DB(self.engine) as db:
            row = self._session(db, sid)
            root = Path(row.output_root)
            if parent_id is None:
                parent_path = root
            else:
                parent = db.get(Folder, parent_id)
                if parent is None or parent.session_id != sid:
                    raise NotFound(f"folder {parent_id}")
                parent_path = Path(parent.path)
            target = parent_path / clean
            existing = db.exec(
                select(Folder).where(Folder.session_id == sid, Folder.path == str(target))
            ).first()
            if existing is not None:
                raise BadRequest(f"folder exists: {_rel(target, root)}")
            dec = Decision(session_id=sid, action="mkdir")
            db.add(dec)
            db.commit()
            db.refresh(dec)
            made = self._executor(row).mkdir(target, decision_id=dec.id)
            f = self._add_folder(db, sid, made, root, "user")
            db.commit()
            db.refresh(f)
            pipe = self.pipelines.get(sid)
            if pipe is not None:
                pipe.on_folder_created(f, _rel(made, root))
            assert f.id is not None
            return FolderOut(id=f.id, path=_rel(made, root), name=made.name, depth=f.depth,
                             created_by="user")

    # ---------- cards ----------
    def _manual_edges(self, sid: int, db: DB, root: Path) -> dict[str, Edge]:
        edges: dict[str, Edge] = {
            "left": Edge(label="Choose folder…", action="picker"),
            "up": Edge(label="Later", action="defer"),
            "down": Edge(label="Trash", action="trash"),
        }
        last = self._last_target.get(sid)
        if last is not None:
            folder = db.get(Folder, last)
            if folder is not None:
                rel = _rel(Path(folder.path), root) or "(output root)"
                edges["right"] = Edge(label=f"Move to {rel}", action="pick", target_folder_id=last)
        return edges

    def _card(self, f: File, sid: int, db: DB, root: Path,
              sug: Suggestion | None = None) -> Card:
        assert f.id is not None
        info = FileInfo(id=f.id, name=Path(f.path).name, rel_path=f.rel_path, size=f.size,
                        mtime=f.mtime, ext=f.ext, is_bundle=f.is_bundle)
        if sug is None:
            return Card(id=f"m{f.id}", kind="manual", file=info, reason="Pick a destination",
                        edges=self._manual_edges(sid, db, root))
        cands = [Candidate(folder_id=c["folder_id"], path=c["path"], p=c["p"])
                 for c in json.loads(sug.candidates_json)]

        def pick(c: Candidate, prefix: str = "") -> Edge:
            return Edge(label=f"{prefix}{c.path or '(output root)'}", action="pick",
                        target_folder_id=c.folder_id)

        trash = Edge(label="Trash", action="trash")
        choice_edges: dict[str, Edge] = {"down": trash}
        keys: dict[str, Edge] = {}
        for i, d in enumerate(("right", "up", "left")):
            if i < len(cands):
                choice_edges[d] = pick(cands[i])
                keys[str(i + 1)] = choice_edges[d]
        if sug.tier == "confirm":
            edges: dict[str, Edge] = {
                "right": pick(cands[0], "Move to "),
                "up": Edge(label="Later", action="defer"),
                "left": Edge(label="Not this", action="flip"),
                "down": trash,
            }
            flip: dict[str, Edge] | None = choice_edges
        elif sug.tier == "choices":
            edges, flip = choice_edges, None
        else:
            edges = {
                "right": Edge(label=f"Send to {INBOX_NAME}", action="inbox"),
                "left": Edge(label="Choose folder…", action="picker"),
                "down": trash,
            }
            if cands:
                edges["up"] = pick(cands[0], "Maybe ")
            flip = None
        return Card(
            id=f"m{f.id}", kind=sug.tier, file=info,
            reason=sug.reason, edges=edges, flip_edges=flip,
            confidence=sug.confidence, candidates=cands[:3], keys=keys,
        )

    def cards(self, sid: int, limit: int = 20) -> list[Card]:
        with DB(self.engine) as db:
            row = self._session(db, sid)
            files = db.exec(
                select(File).where(File.session_id == sid, col(File.status).in_(SHOWABLE))
            ).all()

            def key(f: File) -> tuple[int, str, str]:
                p = PurePosixPath(f.rel_path)
                return (1 if f.status == FileStatus.skipped else 0, str(p.parent).casefold(),
                        p.name.casefold())

            ordered = sorted(files, key=key)[:limit]
            latest: dict[int, Suggestion] = {}
            ids = [f.id for f in ordered]
            for sug in db.exec(select(Suggestion).where(col(Suggestion.file_id).in_(ids))
                               .order_by(col(Suggestion.id))).all():
                latest[sug.file_id] = sug  # ascending id -> last write wins = newest
            root = Path(row.output_root)
            return [self._card(f, sid, db, root, latest.get(f.id or -1)) for f in ordered]

    def _inbox_folder(self, db: DB, sid: int, root: Path) -> Folder:
        path = root / INBOX_NAME
        fo = db.exec(select(Folder).where(Folder.session_id == sid,
                                          Folder.path == str(path))).first()
        if fo is None:
            fo = self._add_folder(db, sid, path, root, "inbox")
            db.commit()
            db.refresh(fo)
        return fo

    def decide(self, card_id: str, body: DecideIn) -> DecideOut:
        if not card_id.startswith("m") or not card_id[1:].isdigit():
            raise NotFound(f"card {card_id}")
        fid = int(card_id[1:])
        with DB(self.engine) as db:
            f = db.get(File, fid)
            if f is None:
                raise NotFound(f"card {card_id}")
            sid = f.session_id
        with self._lock(sid), DB(self.engine) as db:
            f = db.get(File, fid)
            assert f is not None
            row = self._session(db, sid)
            if f.status not in SHOWABLE:
                raise BadRequest("card not ready or already decided")
            dec = Decision(session_id=sid, file_id=fid, action=body.action,
                           target_folder_id=body.target_folder_id, latency_ms=body.latency_ms)
            db.add(dec)
            db.commit()
            db.refresh(dec)
            assert dec.id is not None
            ex = self._executor(row)
            final: Path | None = None
            if body.action == "pick":
                folder = db.get(Folder, body.target_folder_id) if body.target_folder_id else None
                if folder is None or folder.session_id != sid:
                    db.delete(dec)
                    db.commit()
                    raise BadRequest("target_folder_id required")
                final = ex.move(Path(f.path), Path(folder.path), decision_id=dec.id)
                f.status = FileStatus.decided
                folder.n_files += 1
                db.add(folder)
                assert folder.id is not None
                self._last_target[sid] = folder.id
                db.add(IndexedFile(session_id=sid, folder_id=folder.id, path=str(final),
                                   name=final.name, ext=f.ext, content_hash=f.content_hash,
                                   file_id=fid))
                pipe = self.pipelines.get(sid)
                if pipe is not None:
                    pipe.on_placed(fid, folder.id, str(final))
            elif body.action == "inbox":
                inbox = self._inbox_folder(db, sid, Path(row.output_root))
                dec.target_folder_id = inbox.id
                db.add(dec)
                db.commit()  # release the write lock before the executor journals
                final = ex.move(Path(f.path), Path(inbox.path), decision_id=dec.id)
                f.status = FileStatus.decided
            elif body.action == "trash":
                final = ex.trash(Path(f.path), decision_id=dec.id)
                f.status = FileStatus.decided
            elif body.action == "defer":
                f.status = FileStatus.skipped
            else:
                db.delete(dec)
                db.commit()
                raise BadRequest(f"unsupported action in M0: {body.action}")
            if final is not None:
                f.path = str(final)
            db.add(f)
            db.commit()
            return DecideOut(decision_id=dec.id, final_path=str(final) if final else None,
                             progress=self._progress(db, sid))

    def undo(self, sid: int, steps: int = 1) -> UndoOut:
        undone_ops: list[int] = []
        with self._lock(sid), DB(self.engine) as db:
            row = self._session(db, sid)
            ex = self._executor(row)
            decisions = db.exec(
                select(Decision)
                .where(Decision.session_id == sid, col(Decision.undone).is_(False))
                .order_by(col(Decision.id).desc())
                .limit(steps)
            ).all()
            for dec in decisions:
                ops = db.exec(
                    select(Op).where(Op.decision_id == dec.id, Op.status == OpStatus.done)
                ).all()
                if ops:
                    ids = ex.undo(1)
                    undone_ops.extend(ids)
                    for op in db.exec(select(Op).where(col(Op.id).in_(ids))).all():
                        if op.type in (OpType.move, OpType.trash):
                            f = db.exec(select(File).where(
                                File.session_id == sid, File.path == op.dst)).first()
                            if f is not None:
                                assert f.id is not None
                                f.path = op.src
                                f.status = FileStatus.ready
                                db.add(f)
                                for ix in db.exec(select(IndexedFile).where(
                                        IndexedFile.session_id == sid,
                                        IndexedFile.path == op.dst)).all():
                                    pipe = self.pipelines.get(sid)
                                    if pipe is not None:
                                        pipe.on_unplaced(f.id, ix.folder_id, op.dst)
                                    db.delete(ix)
                        elif op.type == OpType.mkdir:
                            for fo in db.exec(select(Folder).where(
                                    Folder.session_id == sid, Folder.path == op.dst)).all():
                                pipe = self.pipelines.get(sid)
                                if pipe is not None and fo.id is not None:
                                    pipe.on_folder_removed(fo.id)
                                if self._last_target.get(sid) == fo.id:
                                    self._last_target.pop(sid, None)
                                db.delete(fo)
                if dec.action == "pick" and dec.target_folder_id is not None:
                    tgt = db.get(Folder, dec.target_folder_id)
                    if tgt is not None:
                        tgt.n_files = max(0, tgt.n_files - 1)
                        db.add(tgt)
                if dec.action == "defer" and dec.file_id is not None:
                    f = db.get(File, dec.file_id)
                    if f is not None and f.status == FileStatus.skipped:
                        f.status = FileStatus.ready
                        db.add(f)
                dec.undone = True
                db.add(dec)
                db.commit()
            return UndoOut(undone_ops=undone_ops, progress=self._progress(db, sid))

    # ---------- trees ----------
    def tree(self, sid: int, side: str) -> TreeNode:
        with DB(self.engine) as db:
            row = self._session(db, sid)
            if side == "input":
                root = Path(row.input_root)
                node = TreeNode(name=root.name or str(root), path="", is_dir=True)
                files = db.exec(select(File).where(
                    File.session_id == sid, col(File.status).in_(REMAINING))).all()
                for f in sorted(files, key=lambda f: f.rel_path.casefold()):
                    parts = PurePosixPath(f.rel_path).parts
                    cur = node
                    cur.count += 1
                    for i, part in enumerate(parts[:-1]):
                        nxt = next((c for c in cur.children if c.name == part and c.is_dir), None)
                        if nxt is None:
                            nxt = TreeNode(name=part, path="/".join(parts[: i + 1]), is_dir=True)
                            cur.children.append(nxt)
                        nxt.count += 1
                        cur = nxt
                    cur.children.append(TreeNode(id=f.id, name=parts[-1], path=f.rel_path,
                                                 is_dir=False, is_bundle=f.is_bundle))
                return node
            if side != "output":
                raise BadRequest("side must be input|output")
            root = Path(row.output_root)
            folders = db.exec(select(Folder).where(Folder.session_id == sid)).all()
            nodes: dict[int, TreeNode] = {}
            top: TreeNode | None = None
            for fo in sorted(folders, key=lambda x: x.depth):
                assert fo.id is not None
                p = Path(fo.path)
                try:
                    count = sum(1 for e in p.iterdir() if not e.is_dir() or e.suffix)
                except OSError:
                    count = 0
                n = TreeNode(id=fo.id, name=p.name if fo.depth else (p.name or str(p)),
                             path=_rel(p, root), is_dir=True, count=count,
                             is_new=fo.created_by != "existing")
                nodes[fo.id] = n
                if fo.parent_id is not None and fo.parent_id in nodes:
                    nodes[fo.parent_id].children.append(n)
                elif top is None:
                    top = n
            for n in nodes.values():
                n.children.sort(key=lambda c: c.name.casefold())
            if top is None:
                raise NotFound("output root folder")
            return top

    def file_path(self, fid: int) -> Path:
        with DB(self.engine) as db:
            f = db.get(File, fid)
            if f is None:
                raise NotFound(f"file {fid}")
            return Path(f.path)
