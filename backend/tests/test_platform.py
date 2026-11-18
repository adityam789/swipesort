from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from swipesort.platform import Platform, get_platform
from swipesort.platform.fallback import FallbackPlatform
from swipesort.platform.macos import MacOSPlatform, parse_mdls_list
from swipesort.platform.windows import WindowsPlatform, parse_zone_identifier


def test_get_platform_is_cached_platform() -> None:
    p = get_platform()
    assert isinstance(p, Platform)
    assert get_platform() is p
    expected = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "fallback")
    assert p.name == expected


def test_all_modules_are_protocol_compatible() -> None:
    for cls in (FallbackPlatform, MacOSPlatform, WindowsPlatform):
        assert isinstance(cls(), Platform)


def test_fallback_basics(tmp_path: Path) -> None:
    fb = FallbackPlatform()
    f = tmp_path / "a.txt"
    f.write_text("hi")
    assert fb.ocr(f) == ""
    assert fb.source_metadata(f).where_froms == []
    assert fb.user_tags(f) == []
    assert fb.thumbnail(f, tmp_path / "t.png") is None
    assert fb.is_cloud_placeholder(f) is False
    assert fb.is_locked(f) is False
    assert fb.install_background_agent() is False
    assert fb.long_path(f) == f
    fb.notify("t", "b")


def test_fallback_thumbnail_image(tmp_path: Path) -> None:
    pil = pytest.importorskip("PIL.Image")
    src = tmp_path / "img.png"
    pil.new("RGB", (400, 200), "red").save(src)
    out = tmp_path / "thumbs" / "img.png"
    assert FallbackPlatform().thumbnail(src, out, 64) == out
    with pil.open(out) as im:
        assert max(im.size) <= 64


def test_hidden_dotfile(tmp_path: Path) -> None:
    dot = tmp_path / ".secret"
    dot.write_text("x")
    assert get_platform().is_hidden(dot)
    assert FallbackPlatform().is_hidden(dot)
    visible = tmp_path / "visible.txt"
    visible.write_text("x")
    assert not get_platform().is_hidden(visible)


def test_same_volume(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_text("1")
    b.write_text("2")
    assert get_platform().same_volume(a, b)


def test_long_path_identity_short(tmp_path: Path) -> None:
    assert get_platform().long_path(tmp_path) == tmp_path


def test_check_access(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("1")
    assert get_platform().check_access(tmp_path).ok
    report = get_platform().check_access(tmp_path / "missing")
    assert not report.ok
    assert report.message


def test_parse_mdls_list() -> None:
    assert parse_mdls_list("(null)") == []
    assert parse_mdls_list("") == []
    raw = '(\n    "https://a.example/f.pdf",\n    "https://a.example/"\n)'
    assert parse_mdls_list(raw) == ["https://a.example/f.pdf", "https://a.example/"]


def test_macos_with_mocked_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if "kMDItemUserTags" in args:
            return subprocess.CompletedProcess(args, 0, '(\n    "Red\\n6",\n    Work\n)', "")
        return subprocess.CompletedProcess(args, 0, '(\n    "https://x.test/a"\n)', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    mac = MacOSPlatform()
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert mac.source_metadata(f).where_froms == ["https://x.test/a"]
    assert mac.user_tags(f) == ["Red", "Work"]
    mac.notify("t", 'b"q')
    assert calls[-1][0] == "osascript"


def test_macos_failure_tolerated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(*a: Any, **kw: Any) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", boom)
    mac = MacOSPlatform()
    assert mac.source_metadata(tmp_path).where_froms == []
    assert mac.user_tags(tmp_path) == []
    mac.notify("t", "b")  # falls back to logging


def test_macos_icloud_placeholder(tmp_path: Path) -> None:
    mac = MacOSPlatform()
    assert mac.is_cloud_placeholder(tmp_path / ".report.pdf.icloud")
    assert not mac.is_cloud_placeholder(tmp_path / "report.pdf.icloud")
    assert not mac.is_cloud_placeholder(tmp_path / "report.pdf")


def test_windows_zone_identifier_parse() -> None:
    text = "[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl=https://r.test/\r\nHostUrl=https://h.test/f.zip\r\n"
    assert parse_zone_identifier(text).where_froms == ["https://r.test/", "https://h.test/f.zip"]


def test_windows_long_path_logic() -> None:
    win = WindowsPlatform()
    short = Path("C:/short/file.txt")
    assert win.long_path(short) == short
    if sys.platform == "win32":
        long = Path("C:\\" + "\\".join(["d" * 50] * 6) + "\\f.txt")
        assert str(win.long_path(long)).startswith("\\\\?\\C:\\")
        unc = Path("\\\\srv\\share\\" + "\\".join(["d" * 50] * 6))
        assert str(win.long_path(unc)).startswith("\\\\?\\UNC\\srv\\share")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_locked_file(tmp_path: Path) -> None:
    f = tmp_path / "locked.txt"
    f.write_text("x")
    win = WindowsPlatform()
    assert win.is_locked(f) is False


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_macos_real_hidden_and_thumbnail_free(tmp_path: Path) -> None:
    f = tmp_path / "v.txt"
    f.write_text("x")
    assert not MacOSPlatform().is_hidden(f)
