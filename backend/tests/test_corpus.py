from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from generate_corpus import generate


def _hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_generate_basic(tmp_path: Path) -> None:
    m = generate(tmp_path / "c", n=60, seed=1)
    assert len(m.entries) == 60
    for e in m.entries:
        assert (tmp_path / "c" / "input" / e.rel_path).exists(), e.rel_path
        assert (tmp_path / "c" / "output" / e.category).is_dir()
    for e in m.examples:
        assert (tmp_path / "c" / "output" / e.rel_path).exists()
    for cat in m.categories:
        assert any(x.category == cat for x in m.examples)
    assert (tmp_path / "c" / "manifest.json").is_file()
    assert any(e.dup_of for e in m.entries)
    assert any(e.near_dup_of for e in m.entries)
    assert any(e.version_of for e in m.entries)
    assert any(e.misleading_name for e in m.entries)
    code = [e for e in m.entries if e.kind == "code_project"]
    for e in code:
        d = tmp_path / "c" / "input" / e.rel_path
        assert d.is_dir() and ((d / ".git").exists() or (d / "package.json").exists())


def test_deterministic(tmp_path: Path) -> None:
    m1 = generate(tmp_path / "a", n=60, seed=7)
    m2 = generate(tmp_path / "b", n=60, seed=7)
    assert m1.model_dump() == m2.model_dump()
    assert _hashes(tmp_path / "a") == _hashes(tmp_path / "b")
    m3 = generate(tmp_path / "c", n=60, seed=8)
    assert m3.model_dump() != m1.model_dump()


def test_exact_dupes_identical(tmp_path: Path) -> None:
    m = generate(tmp_path / "c", n=80, seed=3)
    dupes = [e for e in m.entries if e.dup_of]
    assert dupes
    inp = tmp_path / "c" / "input"
    for e in dupes:
        assert e.dup_of is not None
        assert (inp / e.rel_path).read_bytes() == (inp / e.dup_of).read_bytes()
        assert e.rel_path != e.dup_of


def test_nested(tmp_path: Path) -> None:
    m = generate(tmp_path / "c", n=60, seed=2, nested=True)
    assert "Work/PayPal/Onboarding/Docs" in m.categories
    assert (tmp_path / "c" / "output" / "Work/PayPal/Onboarding/Docs").is_dir()
    assert any(e.category == "Work/PayPal/Onboarding/Docs" for e in m.examples)


def test_default_manifest_format_unchanged(tmp_path: Path) -> None:
    generate(tmp_path / "c", n=30, seed=1)
    text = (tmp_path / "c" / "manifest.json").read_text(encoding="utf-8")
    assert '"hard"' not in text and "generic_name" not in text and "misleading_dir" not in text


def test_hard_deterministic(tmp_path: Path) -> None:
    m1 = generate(tmp_path / "a", n=80, seed=11, hard=True)
    m2 = generate(tmp_path / "b", n=80, seed=11, hard=True)
    assert m1.hard and m1.model_dump() == m2.model_dump()
    assert _hashes(tmp_path / "a") == _hashes(tmp_path / "b")
    assert generate(tmp_path / "c", n=80, seed=12, hard=True).model_dump() != m1.model_dump()
    # hard mode must not change the easy corpus for the same seed
    easy = generate(tmp_path / "e", n=80, seed=11)
    assert easy.model_dump() != m1.model_dump()


def test_hard_corpus(tmp_path: Path) -> None:
    m = generate(tmp_path / "c", n=300, seed=0, hard=True)
    assert len(m.entries) == 300
    inp = tmp_path / "c" / "input"
    for e in m.entries:
        assert (inp / e.rel_path).exists(), e.rel_path
        assert (tmp_path / "c" / "output" / e.category).is_dir()
    for e in m.examples:
        assert (tmp_path / "c" / "output" / e.rel_path).exists()
    assert sum(e.generic_name for e in m.entries) >= 0.10 * len(m.entries)
    assert sum(e.misleading_name for e in m.entries) >= 0.10 * len(m.entries)
    assert any(e.misleading_dir for e in m.entries)
    assert {e.kind for e in m.entries} >= {"invoice", "receipt", "tax_form", "resume", "screenshot"}
    # template variety: some invoices never use the word "invoice"
    plain = [inp / e.rel_path for e in m.entries
             if e.kind == "invoice" and e.rel_path.lower().endswith((".txt", ".md"))]
    assert plain and any(b"invoice" not in p.read_bytes().lower() for p in plain)
