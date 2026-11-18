from __future__ import annotations

import os
from pathlib import Path

import pytest

from swipesort.config import Scan
from swipesort.pipeline.fingerprint import content_hash, quick_hash
from swipesort.pipeline.scanner import list_dirs, scan


class FakePlatform:
    name = "fallback"

    def is_hidden(self, path: Path) -> bool:
        return path.name.startswith(".")

    def is_cloud_placeholder(self, path: Path) -> bool:
        return False


def _w(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _scan(root: Path, cfg: Scan | None = None) -> list[str]:
    return [i.rel_path for i in scan(root, cfg or Scan(), FakePlatform())]  # type: ignore[arg-type]


def test_basic_and_nested(tmp_path: Path) -> None:
    _w(tmp_path / "b.PDF", b"12345")
    _w(tmp_path / "a" / "b" / "c.txt")
    _w(tmp_path / "_Inbox" / "z.md")
    items = scan(tmp_path, Scan(), FakePlatform())  # type: ignore[arg-type]
    assert [i.rel_path for i in items] == ["_Inbox/z.md", "a/b/c.txt", "b.PDF"]
    pdf = items[2]
    assert pdf.ext == "pdf" and pdf.size == 5 and not pdf.is_bundle and pdf.mtime > 0


def test_hidden_and_skip_names(tmp_path: Path) -> None:
    _w(tmp_path / ".hidden")
    _w(tmp_path / ".dotdir" / "f.txt")
    _w(tmp_path / ".DS_Store")
    _w(tmp_path / "Thumbs.db")
    _w(tmp_path / "keep.txt")
    assert _scan(tmp_path) == ["keep.txt"]
    assert ".hidden" in _scan(tmp_path, Scan(skip_hidden=False, skip_names=[]))


def test_swipesort_dir_skipped(tmp_path: Path) -> None:
    _w(tmp_path / ".swipesort" / "trash" / "x.txt")
    _w(tmp_path / "a.txt")
    assert _scan(tmp_path, Scan(skip_hidden=False)) == ["a.txt"]
    assert list_dirs(tmp_path, Scan(skip_hidden=False), FakePlatform()) == []  # type: ignore[arg-type]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_symlinks_skipped(tmp_path: Path) -> None:
    _w(tmp_path / "real.txt")
    (tmp_path / "realdir").mkdir()
    try:
        os.symlink(tmp_path / "real.txt", tmp_path / "link.txt")
        os.symlink(tmp_path / "realdir", tmp_path / "linkdir", target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks")
    assert _scan(tmp_path) == ["real.txt"]
    assert [p.name for p in list_dirs(tmp_path, Scan(), FakePlatform())] == ["realdir"]  # type: ignore[arg-type]


def test_bundles(tmp_path: Path) -> None:
    _w(tmp_path / "proj" / ".git" / "HEAD", b"1234")
    _w(tmp_path / "proj" / "src" / "main.py", b"123456")
    _w(tmp_path / "web" / "package.json", b"{}")
    _w(tmp_path / "web" / "index.js", b"abc")
    _w(tmp_path / "Foo.APP" / "Contents" / "bin", b"1234567890")
    _w(tmp_path / "plain" / "f.txt")
    items = scan(tmp_path, Scan(), FakePlatform())  # type: ignore[arg-type]
    by = {i.rel_path: i for i in items}
    assert sorted(by) == ["Foo.APP", "plain/f.txt", "proj", "web"]
    assert by["proj"].is_bundle and by["proj"].size == 10
    assert by["web"].is_bundle and by["web"].size == 5
    assert by["Foo.APP"].is_bundle and by["Foo.APP"].size == 10
    dirs = [p.relative_to(tmp_path).as_posix() for p in list_dirs(tmp_path, Scan(), FakePlatform())]  # type: ignore[arg-type]
    assert dirs == ["plain"]


def test_permission_error_skipped(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("chmod semantics")
    _w(tmp_path / "ok.txt")
    _w(tmp_path / "locked" / "x.txt")
    (tmp_path / "locked").chmod(0o000)
    try:
        if os.access(tmp_path / "locked", os.R_OK):
            pytest.skip("running as root")
        assert _scan(tmp_path) == ["ok.txt"]
    finally:
        (tmp_path / "locked").chmod(0o755)


def test_deterministic(tmp_path: Path) -> None:
    for n in ["z", "a", "m/q", "m/b", "B"]:
        _w(tmp_path / f"{n}.txt")
    first = _scan(tmp_path)
    assert first == _scan(tmp_path) == sorted(first)


def test_fingerprints(tmp_path: Path) -> None:
    big = os.urandom(300_000)
    a = _w(tmp_path / "a.bin", big)
    b = _w(tmp_path / "b.bin", big)
    c = _w(tmp_path / "c.bin", big[:-1] + bytes([big[-1] ^ 1]))
    assert quick_hash(a) == quick_hash(b) != quick_hash(c)
    assert content_hash(a) == content_hash(b) != content_hash(c)
    assert len(content_hash(a)) == 64
    # small file
    s = _w(tmp_path / "s.txt", b"hi")
    assert quick_hash(s) == quick_hash(s)

    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    for d in (d1, d2):
        _w(d / "x.txt", b"x")
        _w(d / "sub" / "y.txt", b"y")
    assert content_hash(d1) == content_hash(d2)
    _w(d2 / "sub" / "y.txt", b"z")
    assert content_hash(d1) != content_hash(d2)
