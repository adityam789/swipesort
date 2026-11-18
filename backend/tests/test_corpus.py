from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from generate_corpus import generate


def _hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"), key=lambda q: q.as_posix())
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


def _leaves(categories: list[str]) -> list[str]:
    return [c for c in categories if not any(o.startswith(c + "/") for o in categories)]


def test_nested(tmp_path: Path) -> None:
    m = generate(tmp_path / "c", n=60, seed=2, nested=True)
    assert "Work/PayPal/Onboarding/Docs" in m.categories
    assert (tmp_path / "c" / "output" / "Work/PayPal/Onboarding/Docs").is_dir()
    assert any(e.category == "Work/PayPal/Onboarding/Docs" for e in m.examples)


def test_nested_tree_depth_and_examples(tmp_path: Path) -> None:
    m = generate(tmp_path / "c", n=60, seed=2, nested=True)
    assert max(len(c.split("/")) for c in m.categories) >= 4
    leaves = _leaves(m.categories)
    assert leaves == m.categories  # categories lists leaves only
    out = tmp_path / "c" / "output"
    counts = {c: sum(e.category == c for e in m.examples) for c in leaves}
    assert all(1 <= n <= 3 for n in counts.values()), counts
    assert any(n == 1 for n in counts.values())
    for c in leaves:
        assert (out / c).is_dir() and any(f for f in (out / c).iterdir()), c
    for e in m.examples:
        assert e.ancestor_only is None and (out / e.rel_path).exists()
    # the tree the spec calls for
    for c in ("Finance/Taxes/2024", "Finance/Invoices/Initech", "Work/Hooli/Projects/Beacon",
              "School/BIO150/Lectures", "Photos/2024/Family", "Photos/Screenshots", "Code"):
        assert c in leaves


@pytest.mark.parametrize("hard", [False, True])
def test_nested_ancestor_only(tmp_path: Path, hard: bool) -> None:
    m = generate(tmp_path / "c", n=300, seed=0, nested=True, hard=hard)
    assert len(m.entries) == 300
    inp = tmp_path / "c" / "input"
    for e in m.entries:
        assert (inp / e.rel_path).exists(), e.rel_path
        assert e.category in m.categories
    anc = [e for e in m.entries if e.ancestor_only]
    assert 0.10 <= len(anc) / len(m.entries) <= 0.22
    assert len({e.ancestor_only for e in anc}) >= 6
    for e in anc:
        assert e.ancestor_only is not None
        assert e.category.startswith(e.ancestor_only + "/"), (e.category, e.ancestor_only)
    # every leaf is exercised by files whose full path is determinable
    exact = {e.category for e in m.entries if not e.ancestor_only}
    assert exact == set(m.categories)
    # the discriminating content is really there for a careful reader
    text = {e.rel_path: (inp / e.rel_path).read_bytes().lower() for e in m.entries
            if e.kind in ("tax_form", "invoice") and not e.ancestor_only
            and e.rel_path.lower().endswith((".txt", ".md"))}
    for e in m.entries:
        if e.rel_path in text:
            key = {"Finance/Taxes/2024": b"2024", "Finance/Taxes/2025": b"2025",
                   "Finance/Invoices/Acme": b"acme", "Finance/Invoices/Initech": b"initech"}[e.category]
            assert key in text[e.rel_path], e.rel_path


def test_nested_deterministic(tmp_path: Path) -> None:
    for hard in (False, True):
        m1 = generate(tmp_path / f"a{hard}", n=80, seed=5, nested=True, hard=hard)
        m2 = generate(tmp_path / f"b{hard}", n=80, seed=5, nested=True, hard=hard)
        assert m1.model_dump() == m2.model_dump()
        assert _hashes(tmp_path / f"a{hard}") == _hashes(tmp_path / f"b{hard}")
        m3 = generate(tmp_path / f"c{hard}", n=80, seed=6, nested=True, hard=hard)
        assert m3.model_dump() != m1.model_dump()
    assert '"ancestor_only"' in (tmp_path / "aFalse" / "manifest.json").read_text(encoding="utf-8")


def _stable_digest(root: Path) -> str:
    """Hash of the manifest, every path, and the bytes of files whose encoding does not depend on
    the installed libjpeg/zlib/python-docx versions."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*"), key=lambda q: q.as_posix()):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".docx", ".pptx"}:
                h.update(p.read_bytes())
    return h.hexdigest()


# Computed on the generator before nested mode was reworked: nested=False output must not drift.
NON_NESTED_DIGESTS = {
    (60, 1, False): "a2afe244dadeafcd31d5ce2779a261d9ebac05340af8a14861ef86fc84a555fa",
    (80, 11, True): "4d8bf8fa015afca42c567bdb19667c926ccf6fbb9c1b026f8c494816bb639beb",
}


@pytest.mark.parametrize(("n", "seed", "hard"), sorted(NON_NESTED_DIGESTS))
def test_non_nested_output_unchanged(tmp_path: Path, n: int, seed: int, hard: bool) -> None:
    generate(tmp_path / "c", n=n, seed=seed, hard=hard)
    assert _stable_digest(tmp_path / "c") == NON_NESTED_DIGESTS[(n, seed, hard)]
    assert "ancestor_only" not in (tmp_path / "c" / "manifest.json").read_text(encoding="utf-8")


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
