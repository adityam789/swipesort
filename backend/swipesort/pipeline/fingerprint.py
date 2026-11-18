"""Content fingerprints. quick_hash is cheap (xxhash64); content_hash is full BLAKE3."""

from __future__ import annotations

import os
from pathlib import Path

import blake3
import xxhash

_EDGE = 64 * 1024
_CHUNK = 1024 * 1024


def quick_hash(path: Path) -> str:
    """xxhash64 over size + first/last 64KB. Files only."""
    size = path.stat().st_size
    h = xxhash.xxh64()
    h.update(size.to_bytes(8, "little"))
    with path.open("rb") as f:
        h.update(f.read(_EDGE))
        if size > _EDGE:
            f.seek(max(_EDGE, size - _EDGE))
            h.update(f.read(_EDGE))
    return str(h.hexdigest())


def _file_blake3(path: Path) -> str:
    h = blake3.blake3()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return str(h.hexdigest())


def _walk_files(root: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            if e.is_symlink():
                continue
            p = Path(e.path)
            if e.is_dir(follow_symlinks=False):
                stack.append(p)
            elif e.is_file(follow_symlinks=False):
                out.append((p.relative_to(root).as_posix(), p))
    out.sort(key=lambda t: t[0])
    return out


def content_hash(path: Path) -> str:
    """Full BLAKE3 hex. For a directory: BLAKE3 over sorted (rel_path, file hash) pairs."""
    if not path.is_dir():
        return _file_blake3(path)
    h = blake3.blake3()
    for rel, p in _walk_files(path):
        try:
            fh = _file_blake3(p)
        except OSError:
            continue
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(fh.encode("ascii"))
        h.update(b"\n")
    return str(h.hexdigest())
