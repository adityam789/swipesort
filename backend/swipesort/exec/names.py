"""Cross-OS filename sanitizing and collision handling (pure functions).

Windows rules are applied on every OS so that a tree created on a Mac still works on Windows.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePath

MAX_NAME_LEN = 255

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _strip_trailing(name: str) -> str:
    return name.rstrip(" .")


def sanitize_name(name: str) -> str:
    """Return a name that is valid on Windows and POSIX alike."""
    out = _strip_trailing(_INVALID.sub("_", name))
    head, dot, rest = out.partition(".")
    if head.rstrip(" ").upper() in _RESERVED:
        out = head.rstrip(" ") + "_" + dot + rest
    if len(out) > MAX_NAME_LEN:
        suffix = PurePath(out).suffix
        if len(suffix) > 32:
            suffix = ""
        stem = out[: len(out) - len(suffix)]
        out = stem[: MAX_NAME_LEN - len(suffix)] + suffix
        out = _strip_trailing(out)
    return out or "_"


def resolve_collision(dst_dir: Path, name: str, is_dir: bool = False) -> Path:
    """Return a path in dst_dir for `name` that collides (case-insensitively) with nothing.

    Appends " (2)", " (3)", ... before the suffix (for directories: at the end).
    """
    try:
        taken = {e.casefold() for e in os.listdir(dst_dir)}
    except (FileNotFoundError, NotADirectoryError):
        taken = set()
    if name.casefold() not in taken:
        return dst_dir / name
    if is_dir:
        stem, suffix = name, ""
    else:
        p = PurePath(name)
        stem, suffix = p.stem, p.suffix
    n = 2
    while True:
        cand = f"{stem} ({n}){suffix}"
        if cand.casefold() not in taken:
            return dst_dir / cand
        n += 1


def _norm_parts(path: Path | str) -> tuple[str, ...]:
    return tuple(p.casefold() for p in Path(os.path.abspath(path)).parts)


def is_within(path: Path, root: Path) -> bool:
    """True if `path` is `root` or lies below it. Purely lexical: symlinks are not resolved."""
    pp, rp = _norm_parts(path), _norm_parts(root)
    return len(pp) >= len(rp) and pp[: len(rp)] == rp
