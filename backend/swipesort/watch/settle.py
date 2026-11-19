"""Settle delay: a new file is processed only once it stopped changing.

A tracked path is *settled* when its signature (size + mtime; for a dropped folder: entry
count + total size + newest mtime) has been unchanged for `settle_seconds` AND it can be
opened exclusively (`platform.is_locked` is False: on Windows the browser holds a lock while it
downloads). Every OS event on the path restarts the clock. Pure logic over an injectable clock
and stat function, so it's unit-tested without sleeping.
"""

from __future__ import annotations

import fnmatch
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

Signature = tuple[int, int, int]  # (entries, total size, newest mtime_ns)


def signature(path: Path) -> Signature | None:
    """Size/mtime fingerprint of a file, or of a folder's whole subtree. None if it's gone
    (or is a symlink: never followed)."""
    try:
        st = path.lstat()
    except OSError:
        return None
    if path.is_symlink():
        return None
    if not path.is_dir():
        return (1, st.st_size, st.st_mtime_ns)
    count, total, newest = 0, 0, st.st_mtime_ns
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                est = e.stat(follow_symlinks=False)
            except OSError:
                continue
            count += 1
            newest = max(newest, est.st_mtime_ns)
            if e.is_dir(follow_symlinks=False):
                stack.append(Path(e.path))
            else:
                total += est.st_size
    return (count, total, newest)


def is_ignored(rel_parts: Iterable[str], patterns: list[str]) -> bool:
    """Partial downloads / temp files (`*.crdownload`, `*.part`, ...), matched case-insensitively
    against every path component (Safari's `x.pdf.download/` is a folder)."""
    pats = [p.casefold() for p in patterns]
    return any(fnmatch.fnmatchcase(part.casefold(), p) for part in rel_parts for p in pats)


@dataclass
class _Track:
    sig: Signature | None
    stable_since: float
    first_seen: float


class Settler:
    def __init__(self, settle_seconds: float, is_locked: Callable[[Path], bool],
                 clock: Callable[[], float] = time.monotonic,
                 stat: Callable[[Path], Signature | None] = signature) -> None:
        self.settle_seconds = settle_seconds
        self.is_locked = is_locked
        self.clock = clock
        self.stat = stat
        self._tracked: dict[Path, _Track] = {}

    def __contains__(self, path: Path) -> bool:
        return path in self._tracked

    def __len__(self) -> int:
        return len(self._tracked)

    def touch(self, path: Path) -> None:
        """An OS event (created / modified / renamed-to) on `path`: (re)start its clock."""
        now = self.clock()
        t = self._tracked.get(path)
        if t is None:
            self._tracked[path] = _Track(sig=self.stat(path), stable_since=now, first_seen=now)
        else:
            t.sig, t.stable_since = self.stat(path), now

    def forget(self, path: Path) -> None:
        self._tracked.pop(path, None)

    def poll(self) -> list[Path]:
        """Paths that have settled since the last poll (they stop being tracked). Vanished paths
        (renamed away, deleted) are dropped silently."""
        now = self.clock()
        done: list[Path] = []
        for path, t in list(self._tracked.items()):
            sig = self.stat(path)
            if sig is None:
                del self._tracked[path]
                continue
            if sig != t.sig:
                t.sig, t.stable_since = sig, now
                continue
            if now - t.stable_since < self.settle_seconds:
                continue
            if not path.is_dir() and self.is_locked(path):
                continue  # still held open by its writer: keep waiting
            del self._tracked[path]
            done.append(path)
        return done
