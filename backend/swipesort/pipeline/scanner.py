"""Recursive scan (SPEC §7). Bundles are single items; symlinks are never followed."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from swipesort.config import Scan
from swipesort.platform.base import Platform

SWIPESORT_DIR = ".swipesort"


class ScanItem(BaseModel):
    path: Path
    rel_path: str  # posix, relative to scan root
    is_bundle: bool
    size: int  # bundle = total size of contents
    mtime: float
    ext: str  # lowercase, no dot ("" for none / bundles)


def _skipped(name: str, path: Path, cfg: Scan, platform: Platform) -> bool:
    if name == SWIPESORT_DIR or name in cfg.skip_names:
        return True
    if cfg.skip_hidden and platform.is_hidden(path):
        return True
    return bool(cfg.skip_cloud_placeholders and platform.is_cloud_placeholder(path))


def _is_bundle(path: Path, names: set[str], cfg: Scan) -> bool:
    lower = path.name.lower()
    if any(lower.endswith(s.lower()) for s in cfg.bundle_suffixes):
        return True
    return any(m in names for m in cfg.bundle_markers)


def _entries(d: Path) -> list[os.DirEntry[str]] | None:
    try:
        with os.scandir(d) as it:
            return sorted(it, key=lambda e: e.name)
    except OSError:  # PermissionError, vanished dir, ...
        return None


def _bundle_stats(d: Path) -> tuple[int, float]:
    """Total size and newest mtime of a bundle's contents (symlinks ignored)."""
    total = 0
    newest = 0.0
    try:
        newest = d.stat().st_mtime
    except OSError:
        pass
    stack = [d]
    while stack:
        cur = stack.pop()
        entries = _entries(cur)
        if entries is None:
            continue
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    total += st.st_size
                    newest = max(newest, st.st_mtime)
            except OSError:
                continue
    return total, newest


def scan(root: Path, settings: Scan, platform: Platform) -> list[ScanItem]:
    """Return files and bundle folders under root, sorted by rel_path."""
    items: list[ScanItem] = []
    stack = [root]
    while stack:
        d = stack.pop()
        entries = _entries(d)
        if entries is None:
            continue
        for e in entries:
            p = Path(e.path)
            try:
                if e.is_symlink() or _skipped(e.name, p, settings, platform):
                    continue
                rel = p.relative_to(root).as_posix()
                if e.is_dir(follow_symlinks=False):
                    sub = _entries(p)
                    if sub is None:
                        continue
                    if _is_bundle(p, {s.name for s in sub}, settings):
                        size, mtime = _bundle_stats(p)
                        items.append(
                            ScanItem(path=p, rel_path=rel, is_bundle=True, size=size,
                                     mtime=mtime, ext="")
                        )
                    else:
                        stack.append(p)
                elif e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    items.append(
                        ScanItem(path=p, rel_path=rel, is_bundle=False, size=st.st_size,
                                 mtime=st.st_mtime, ext=p.suffix.lower().lstrip("."))
                    )
            except OSError:
                continue
    items.sort(key=lambda i: i.rel_path)
    return items


def list_dirs(root: Path, settings: Scan, platform: Platform) -> list[Path]:
    """All non-bundle, non-hidden subdirectories under root (sorted), excluding root."""
    out: list[Path] = []
    stack = [root]
    while stack:
        d = stack.pop()
        entries = _entries(d)
        if entries is None:
            continue
        for e in entries:
            p = Path(e.path)
            try:
                if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                    continue
                if e.name == SWIPESORT_DIR:
                    continue
                if settings.skip_hidden and platform.is_hidden(p):
                    continue
                sub = _entries(p)
                if sub is None or _is_bundle(p, {s.name for s in sub}, settings):
                    continue
            except OSError:
                continue
            out.append(p)
            stack.append(p)
    out.sort(key=lambda p: p.relative_to(root).as_posix())
    return out
