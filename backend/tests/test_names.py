from __future__ import annotations

from pathlib import Path

import pytest

from swipesort.exec.names import is_within, resolve_collision, sanitize_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Invoices", "Invoices"),
        ('a<b>c:d"e/f\\g|h?i*j', "a_b_c_d_e_f_g_h_i_j"),
        ("tab\there\nnl", "tab_here_nl"),
        ("trailing. . ", "trailing"),
        ("dots...", "dots"),
        ("", "_"),
        ("...", "_"),
        (" ", "_"),
        ("CON", "CON_"),
        ("con", "con_"),
        ("Nul", "Nul_"),
        ("com1", "com1_"),
        ("LPT9", "LPT9_"),
        ("con.txt", "con_.txt"),
        ("AUX.tar.gz", "AUX_.tar.gz"),
        ("CONSOLE", "CONSOLE"),
        ("com10", "com10"),
        ("Taxes 2024", "Taxes 2024"),
    ],
)
def test_sanitize(raw: str, expected: str) -> None:
    assert sanitize_name(raw) == expected


def test_sanitize_cap_255_keeps_suffix() -> None:
    out = sanitize_name("a" * 400 + ".pdf")
    assert len(out) == 255
    assert out.endswith(".pdf")
    assert len(sanitize_name("b" * 400)) == 255


def test_sanitize_idempotent() -> None:
    for raw in ["CON", "a:b", "x. ", "", "con.txt"]:
        once = sanitize_name(raw)
        assert sanitize_name(once) == once


def test_collision_none(tmp_path: Path) -> None:
    assert resolve_collision(tmp_path, "a.txt") == tmp_path / "a.txt"
    assert resolve_collision(tmp_path / "missing", "a.txt") == tmp_path / "missing" / "a.txt"


def test_collision_sequence(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    assert resolve_collision(tmp_path, "a.txt") == tmp_path / "a (2).txt"
    (tmp_path / "a (2).txt").write_text("x")
    assert resolve_collision(tmp_path, "a.txt") == tmp_path / "a (3).txt"


def test_collision_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "Report.pdf").write_text("x")
    assert resolve_collision(tmp_path, "report.pdf") == tmp_path / "report (2).pdf"
    assert resolve_collision(tmp_path, "REPORT.PDF") == tmp_path / "REPORT (2).PDF"


def test_collision_dir_suffix_at_end(tmp_path: Path) -> None:
    (tmp_path / "proj.app").mkdir()
    assert resolve_collision(tmp_path, "proj.app", is_dir=True) == tmp_path / "proj.app (2)"
    (tmp_path / ".env").write_text("x")
    assert resolve_collision(tmp_path, ".env") == tmp_path / ".env (2)"


def test_is_within(tmp_path: Path) -> None:
    root = tmp_path / "Root"
    assert is_within(root, root)
    assert is_within(root / "a" / "b.txt", root)
    assert is_within(tmp_path / "root" / "x", root)  # case-insensitive
    assert not is_within(tmp_path / "Rootless", root)
    assert not is_within(tmp_path, root)
    assert not is_within(root / ".." / "other", root)
