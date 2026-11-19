"""OS file events for watched folders via watchdog: FSEvents on macOS,
ReadDirectoryChangesW on Windows (inotify / polling elsewhere).

Every event is mapped to a *unit*: the top-level entry of the watched folder it belongs to. A
file is its own unit; anything inside a dropped folder maps to that folder, so a `.git` checkout
or an `.app` bundle settles and moves as one item (bundles are never scattered), and a
plain folder is scanned as a whole once it stopped changing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from swipesort.config import Scan
from swipesort.pipeline.scanner import SWIPESORT_DIR
from swipesort.platform.base import Platform
from swipesort.watch.settle import is_ignored

log = logging.getLogger(__name__)


def unit_for(path: Path, root: Path, scan_cfg: Scan, platform: Platform, ignore: list[str],
             exclude: list[Path] | None = None) -> Path | None:
    """The top-level entry of `root` that `path` belongs to, or None when it must be ignored:
    outside the root (or the root itself), partial-download / temp names, hidden or system
    entries, cloud placeholders, symlinks, our own `.swipesort/`, or inside `exclude` (an
    output tree nested in the watched folder)."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None
    for ex in exclude or []:
        if path == ex or path.is_relative_to(ex):
            return None
    if is_ignored(parts, ignore):
        return None
    if any(p == SWIPESORT_DIR or p in scan_cfg.skip_names for p in parts):
        return None
    unit = root / parts[0]
    cur = root
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            return None
        if scan_cfg.skip_hidden and (part.startswith(".") or platform.is_hidden(cur)):
            return None
    if scan_cfg.skip_cloud_placeholders and platform.is_cloud_placeholder(unit):
        return None
    return unit


class _Handler(FileSystemEventHandler):
    def __init__(self, on_path: Callable[[Path], None]) -> None:
        self.on_path = on_path

    def _emit(self, raw: str | bytes) -> None:
        p = raw.decode() if isinstance(raw, bytes) else raw
        if p:
            try:
                self.on_path(Path(p))
            except Exception:
                log.exception("watch event handler failed for %s", p)

    def on_created(self, event: FileSystemEvent) -> None:
        self._emit(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:  # a folder's mtime changes whenever its children do
            self._emit(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        # `report.pdf.crdownload` -> `report.pdf`: the final name is what gets processed
        self._emit(event.dest_path)

    def on_closed(self, event: FileSystemEvent) -> None:  # inotify: writer closed the file
        self._emit(event.src_path)


class FolderWatcher:
    """One recursive watchdog observer per watched folder."""

    def __init__(self, root: Path, on_path: Callable[[Path], None]) -> None:
        self.root = root
        self._observer: BaseObserver = Observer()
        self._observer.schedule(_Handler(on_path), str(root), recursive=True)

    def start(self) -> None:
        self._observer.daemon = True
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=5)

    @property
    def alive(self) -> bool:
        return self._observer.is_alive()
