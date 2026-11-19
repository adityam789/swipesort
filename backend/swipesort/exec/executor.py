"""The ONLY module allowed to mutate the filesystem (CLAUDE.md hard rule).

Every mutation is journaled: an Op row is written with status=intent, the action is performed,
then the row is flipped to done (or failed). Undo replays done ops LIFO in reverse.
Crash recovery (`recover()`) reconciles leftover `intent` rows on startup.

CONTRACT (implemented by the executor agent; used by api/ and tests):

class Executor:
    def __init__(self, engine: Engine, session_id: int, input_root: Path, output_root: Path,
                 platform: Platform | None = None, settings: Settings | None = None) -> None
    def move(self, src: Path, dst_dir: Path, decision_id: int | None = None) -> Path
        # moves file OR bundle dir into dst_dir; resolves name collisions (case-insensitive,
        # appends " (2)", " (3)"...); same-volume rename, else copy -> verify BLAKE3 -> unlink.
        # creates missing dst_dir parents via journaled mkdir ops. Returns final path.
    def mkdir(self, path: Path, decision_id: int | None = None) -> Path  # name sanitized via names.py
    def trash(self, src: Path, decision_id: int | None = None) -> Path
        # moves to <output_root>/.swipesort/trash/<session_id>/<rel_path from its root>
    def undo(self, steps: int = 1) -> list[int]
        # undoes the last `steps` *decisions* (all ops sharing a decision_id are one step; ops with
        # decision_id None are their own step). Reverses moves/trash, removes created dirs if empty.
        # Marks ops undone. Returns undone op ids.
    def undo_all(self) -> list[int]
    def recover(self) -> None
    def purge_trash(self, older_than_days: int | None = None) -> int  # only hard delete path

Errors: raise ExecError subclasses: OutsideRootsError, SymlinkError, LockedFileError, ExecError.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from blake3 import blake3
from sqlalchemy import or_
from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from swipesort.config import Settings, get_settings
from swipesort.db.models import Op, OpStatus, OpType
from swipesort.exec import journal, trash
from swipesort.exec.errors import (
    ExecError,
    LockedFileError,
    OutsideRootsError,
    SymlinkError,
    UndoConflictError,
)
from swipesort.exec.names import is_within, resolve_collision, sanitize_name
from swipesort.platform.base import Platform

log = logging.getLogger(__name__)

_CHUNK = 1024 * 1024


def _hash_file(path: Path) -> str:
    h = blake3()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


class Executor:
    def __init__(
        self,
        engine: Engine,
        session_id: int,
        input_root: Path,
        output_root: Path,
        platform: Platform | None = None,
        settings: Settings | None = None,
    ) -> None:
        if platform is None:
            from swipesort.platform import get_platform

            platform = get_platform()
        self.engine = engine
        self.session_id = session_id
        self.input_root = Path(os.path.abspath(input_root))
        self.output_root = Path(os.path.abspath(output_root))
        self.platform: Platform = platform
        self.settings = settings or get_settings()
        self._roots = (self.input_root, self.output_root)

    # ------------------------------------------------------------------ helpers

    def _lp(self, p: Path) -> Path:
        return self.platform.long_path(p)

    def _abs(self, p: Path) -> Path:
        return Path(os.path.abspath(p))

    def _root_of(self, p: Path) -> Path:
        root = trash.containing_root(p, self._roots)
        if root is None:
            raise OutsideRootsError(f"{p} is outside the session roots")
        return root

    def _check_no_symlinks(self, p: Path) -> None:
        """Refuse if p or any ancestor below its root is a symlink."""
        root = self._root_of(p)
        cur = root
        for part in p.parts[len(root.parts) :]:
            cur = cur / part
            if self._lp(cur).is_symlink():
                raise SymlinkError(f"{cur} is a symlink")

    def _check_src(self, src: Path) -> None:
        self._root_of(src)
        if self._lp(src).is_symlink():
            raise SymlinkError(f"{src} is a symlink")
        self._check_no_symlinks(src)
        if not os.path.lexists(self._lp(src)):
            raise ExecError(f"{src} does not exist")

    def _retry_locked(self, fn: Callable[[], object], path: Path, check_lock: bool) -> None:
        retries = self.settings.executor.locked_retries
        backoff = self.settings.executor.locked_backoff_s
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                if check_lock and self.platform.is_locked(path):
                    raise PermissionError(f"{path} is locked")
                fn()
                return
            except PermissionError as e:
                last = e
                if attempt < retries:
                    time.sleep(backoff * (2**attempt))
        raise LockedFileError(f"{path} is locked: {last}")

    # ------------------------------------------------------------ physical moves

    def _manifest(self, root: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        base = self._lp(root)
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            d = Path(dirpath)
            for name in dirnames + filenames:
                p = d / name
                rel = p.relative_to(base).as_posix()
                if p.is_symlink():
                    out[rel] = "l:" + os.readlink(p)
                elif p.is_dir():
                    out[rel] = "d"
                else:
                    out[rel] = "f:" + _hash_file(p)
        return out

    def _remove_partial(self, p: Path) -> None:
        """Remove a copy this executor just created (never user data)."""
        lp = self._lp(p)
        if lp.is_symlink() or lp.is_file():
            os.unlink(lp)
        elif lp.is_dir():
            shutil.rmtree(lp)

    def _copy_verify_remove(self, src: Path, dst: Path) -> None:
        is_dir = self._lp(src).is_dir()
        s, d = self._lp(src), self._lp(dst)
        try:
            if is_dir:
                self._retry_locked(
                    lambda: shutil.copytree(s, d, symlinks=True, copy_function=shutil.copy2),
                    src,
                    check_lock=False,
                )
                ok = self._manifest(src) == self._manifest(dst)
            else:
                self._retry_locked(lambda: shutil.copy2(s, d), src, check_lock=True)
                ok = _hash_file(s) == _hash_file(d)
        except BaseException:
            if os.path.lexists(d):
                self._remove_partial(dst)
            raise
        if not ok:
            self._remove_partial(dst)
            raise ExecError(f"checksum mismatch copying {src} -> {dst}; source kept")
        try:
            if is_dir:
                self._retry_locked(lambda: shutil.rmtree(s), src, check_lock=False)
            else:
                self._retry_locked(lambda: os.unlink(s), src, check_lock=False)
        except LockedFileError:
            if os.path.lexists(s):  # source survived: drop our copy, keep one copy only
                self._remove_partial(dst)
            raise

    def _transfer(self, src: Path, dst: Path) -> None:
        """Physically move src to dst (dst must not exist)."""
        is_dir = self._lp(src).is_dir()
        if self.platform.same_volume(src, dst.parent):
            try:
                self._retry_locked(
                    lambda: os.replace(self._lp(src), self._lp(dst)), src, check_lock=not is_dir
                )
                return
            except OSError as e:
                if e.errno != errno.EXDEV:
                    raise
        self._copy_verify_remove(src, dst)

    # --------------------------------------------------------------- directories

    def _find_dir_ci(self, parent: Path, name: str) -> Path | None:
        """Existing directory in parent whose name matches case-insensitively."""
        try:
            entries = os.listdir(self._lp(parent))
        except OSError:
            return None
        for e in sorted(entries, key=lambda e: (e != name, e)):
            if e.casefold() == name.casefold() and self._lp(parent / e).is_dir():
                return parent / e
        return None

    def _mkdir_op(self, path: Path, decision_id: int | None) -> None:
        with DBSession(self.engine) as db:
            op = journal.begin(db, self.session_id, OpType.mkdir, "", path, decision_id)
            try:
                os.makedirs(self._lp(path), exist_ok=False)
            except BaseException as e:
                journal.fail(db, op, e)
                raise
            journal.finish(db, op)

    def _ensure_dir(self, path: Path, decision_id: int | None) -> Path:
        """Create `path` (and parents) via journaled mkdirs; reuse case-insensitive matches."""
        root = self._root_of(path)
        cur = root
        if not self._lp(cur).exists():
            self._mkdir_op(cur, decision_id)
        for part in path.parts[len(root.parts) :]:
            found = self._find_dir_ci(cur, part)
            if found is None:
                if os.path.lexists(self._lp(cur / part)):
                    raise ExecError(f"{cur / part} exists and is not a directory")
                name = sanitize_name(part)
                found = self._find_dir_ci(cur, name)
                if found is None:
                    found = resolve_collision(cur, name, is_dir=True)
                    self._mkdir_op(found, decision_id)
            nxt = found
            if self._lp(nxt).is_symlink():
                raise SymlinkError(f"{nxt} is a symlink")
            if not self._lp(nxt).is_dir():
                raise ExecError(f"{nxt} exists and is not a directory")
            cur = nxt
        return cur

    # ---------------------------------------------------------------- public API

    def mkdir(self, path: Path, decision_id: int | None = None) -> Path:
        path = self._abs(path)
        self._root_of(path)
        return self._ensure_dir(path, decision_id)

    def move(self, src: Path, dst_dir: Path, decision_id: int | None = None) -> Path:
        return self._relocate(self._abs(src), self._abs(dst_dir), OpType.move, decision_id)

    def trash(self, src: Path, decision_id: int | None = None) -> Path:
        src = self._abs(src)
        self._check_src(src)
        root = self._root_of(src)
        if is_within(src, trash.trash_root(self.output_root)):
            raise ExecError(f"{src} is already in the trash")
        dst_dir = trash.trash_dir_for(self.output_root, self.session_id, root, src)
        app_dir = self.output_root / trash.APP_DIRNAME
        if self._lp(app_dir).is_symlink():
            raise SymlinkError(f"{app_dir} is a symlink")
        os.makedirs(self._lp(trash.trash_root(self.output_root)), exist_ok=True)
        return self._relocate(src, dst_dir, OpType.trash, decision_id)

    def _relocate(
        self, src: Path, dst_dir: Path, op_type: OpType, decision_id: int | None
    ) -> Path:
        self._check_src(src)
        self._root_of(dst_dir)
        if is_within(dst_dir, src):
            raise ExecError(f"cannot move {src} into itself ({dst_dir})")
        is_dir = self._lp(src).is_dir()
        dst_dir = self._ensure_dir(dst_dir, decision_id)
        if is_within(dst_dir, src):
            raise ExecError(f"cannot move {src} into itself ({dst_dir})")
        dst = resolve_collision(dst_dir, src.name, is_dir=is_dir)
        with DBSession(self.engine) as db:
            op = journal.begin(db, self.session_id, op_type, src, dst, decision_id)
            try:
                self._transfer(src, dst)
            except ExecError as e:
                journal.fail(db, op, e)
                raise
            except OSError as e:
                journal.fail(db, op, e)
                raise ExecError(f"{op_type.value} {src} -> {dst} failed: {e}") from e
            journal.finish(db, op)
        return dst

    # ---------------------------------------------------------------------- undo

    def undo(self, steps: int = 1) -> list[int]:
        undone: list[int] = []
        with DBSession(self.engine) as db:
            groups = journal.done_ops_for_undo(db, self.session_id, steps)
            # Strict global LIFO across the chosen decisions, even if their ops interleave.
            for op in sorted((o for g in groups for o in g), key=lambda o: o.id or 0, reverse=True):
                if self._undo_op(db, op) and op.id is not None:
                    undone.append(op.id)
        return undone

    def undo_all(self) -> list[int]:
        return self.undo(2**62)

    def undo_conflict(self, decision_id: int) -> str | None:
        """Why the ops of one decision can't be reversed out of order, or None if they can.
        A later op depends on it when it moved/trashed the file (or something inside the
        bundle) this decision placed, or put anything into a folder this decision created.
        Checked across all sessions: another session may share the output tree."""
        with DBSession(self.engine) as db:
            ops = journal.done_ops_of_decision(db, self.session_id, decision_id)
            if not ops:
                return "nothing to undo"
            first = min(o.id or 0 for o in ops)
            later = db.exec(select(Op).where(
                col(Op.id) > first, col(Op.status) == OpStatus.done,
                or_(col(Op.decision_id).is_(None), col(Op.decision_id) != decision_id))).all()
            for op in ops:
                dst = Path(op.dst)
                if op.type in (OpType.move, OpType.trash):
                    for lo in later:
                        if (lo.id or 0) > (op.id or 0) and lo.src and is_within(Path(lo.src), dst):
                            return f"{dst.name} was moved again after this"
                    if not os.path.lexists(self._lp(dst)):
                        return f"{dst.name} is no longer where it was moved"
                elif op.type == OpType.mkdir:
                    for lo in later:
                        if ((lo.id or 0) > (op.id or 0) and lo.dst and Path(lo.dst) != dst
                                and is_within(Path(lo.dst), dst)):
                            return f"{dst.name}/ was used by a later change"
        return None

    def undo_decision(self, decision_id: int) -> list[int]:
        """Targeted undo (watch mode's per-item Undo): reverse just this decision's ops, LIFO.
        Refuses (UndoConflictError) when later ops depend on them; see undo_conflict()."""
        why = self.undo_conflict(decision_id)
        if why is not None:
            raise UndoConflictError(why)
        undone: list[int] = []
        with DBSession(self.engine) as db:
            for op in journal.done_ops_of_decision(db, self.session_id, decision_id):
                if self._undo_op(db, op) and op.id is not None:
                    undone.append(op.id)
        return undone

    def _undo_op(self, db: DBSession, op: Op) -> bool:
        if op.type == OpType.mkdir:
            p = Path(op.dst)
            self._root_of(p)
            lp = self._lp(p)
            if lp.is_symlink():
                raise SymlinkError(f"{p} is a symlink")
            if lp.is_dir():
                try:
                    os.rmdir(lp)
                except OSError as e:
                    log.warning("undo mkdir: %s not removed (%s)", p, e)
            journal.mark_undone(db, op)
            return True
        if op.type in (OpType.move, OpType.trash):
            cur, orig = Path(op.dst), Path(op.src)
            self._root_of(cur)
            self._root_of(orig)
            if not os.path.lexists(self._lp(cur)):
                journal.fail(db, op, "undo: moved file no longer exists")
                log.warning("undo: %s missing", cur)
                return False
            if self._lp(cur).is_symlink():
                raise SymlinkError(f"{cur} is a symlink")
            is_dir = self._lp(cur).is_dir()
            target_dir = orig.parent
            if not self._lp(target_dir).exists():
                os.makedirs(self._lp(target_dir))
            target = orig
            if os.path.lexists(self._lp(target)):
                target = resolve_collision(target_dir, orig.name, is_dir=is_dir)
                log.warning("undo: %s occupied, restoring as %s", orig, target)
            try:
                self._transfer(cur, target)
            except OSError as e:
                raise ExecError(f"undo of {cur} -> {target} failed: {e}") from e
            if op.type == OpType.trash:
                self._prune_trash_dirs(cur.parent)
            journal.mark_undone(db, op)
            return True
        journal.mark_undone(db, op)  # rmdir ops are never produced; nothing to reverse
        return True

    def _prune_trash_dirs(self, start: Path) -> None:
        """rmdir now-empty directories inside the quarantine (never files, never its root)."""
        root = trash.trash_root(self.output_root)
        cur = start
        while is_within(cur, root) and cur != root and len(cur.parts) > len(root.parts):
            try:
                os.rmdir(self._lp(cur))
            except OSError:
                break
            cur = cur.parent

    # ------------------------------------------------------------------- recover

    def recover(self) -> None:
        with DBSession(self.engine) as db:
            stmt = select(Op).where(
                col(Op.session_id) == self.session_id, col(Op.status) == OpStatus.intent
            )
            for op in list(db.exec(stmt)):
                dst, src = Path(op.dst), Path(op.src)
                dst_there = os.path.lexists(self._lp(dst))
                if op.type == OpType.mkdir:
                    if dst_there:
                        journal.finish(db, op)
                    else:
                        journal.fail(db, op, "recovered: directory was not created")
                    continue
                src_there = os.path.lexists(self._lp(src))
                if dst_there and not src_there:
                    journal.finish(db, op)
                elif src_there and not dst_there:
                    journal.fail(db, op, "recovered: move never happened")
                else:
                    msg = "recovered: ambiguous state (both or neither path exist); left as is"
                    log.warning("op %s %s -> %s: %s", op.id, src, dst, msg)
                    journal.fail(db, op, msg)
                log.info("recovered op %s -> %s", op.id, op.status)

    # --------------------------------------------------------------------- purge

    def purge_trash(self, older_than_days: int | None = None) -> int:
        """The only hard-delete path: removes journaled trash entries older than N days."""
        days = self.settings.trash.purge_days if older_than_days is None else older_than_days
        cutoff = datetime.now(UTC) - timedelta(days=days)
        root = trash.trash_root(self.output_root)
        purged = 0
        with DBSession(self.engine) as db:
            stmt = select(Op).where(
                col(Op.type) == OpType.trash, col(Op.status) == OpStatus.done
            )
            for op in list(db.exec(stmt)):
                ts = op.ts if op.ts.tzinfo else op.ts.replace(tzinfo=UTC)
                p = Path(op.dst)
                if ts > cutoff or not is_within(p, root) or p == root:
                    continue
                lp = self._lp(p)
                if lp.is_symlink() or lp.is_file():
                    os.unlink(lp)
                elif lp.is_dir():
                    shutil.rmtree(lp)
                journal.fail(db, op, "purged")
                self._prune_trash_dirs(p.parent)
                purged += 1
        return purged
