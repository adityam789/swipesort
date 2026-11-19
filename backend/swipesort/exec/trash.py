"""Path helpers for the quarantine trash. Pure; all mutations live in executor.py."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from swipesort.exec.names import is_within

APP_DIRNAME = ".swipesort"
TRASH_DIRNAME = "trash"


def trash_root(output_root: Path) -> Path:
    return output_root / APP_DIRNAME / TRASH_DIRNAME


def session_trash_dir(output_root: Path, session_id: int) -> Path:
    return trash_root(output_root) / str(session_id)


def containing_root(path: Path, roots: Iterable[Path]) -> Path | None:
    """The most specific root containing `path`, or None."""
    best: Path | None = None
    for r in roots:
        if is_within(path, r) and (best is None or len(r.parts) > len(best.parts)):
            best = r
    return best


def relative_to_root(path: Path, root: Path) -> Path:
    """`path` relative to `root`, comparing case-insensitively (path must be within root)."""
    return Path(*path.parts[len(root.parts) :])


def trash_dir_for(output_root: Path, session_id: int, root: Path, src: Path) -> Path:
    """Directory in the quarantine that mirrors `src`'s parent relative to its root."""
    rel_parent = relative_to_root(src, root).parent
    return session_trash_dir(output_root, session_id) / rel_parent
