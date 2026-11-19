"""Dedup engine: exact, near image, near text, version-of, recommendations."""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw

from swipesort.config import Dedup
from swipesort.engine.dedup import DedupItem, DupMatch, find_duplicates, humanize_delta
from swipesort.pipeline.features import compute_features, jaccard
from swipesort.pipeline.fingerprint import content_hash

CFG = Dedup()
NOW = 1_700_000_000.0
WEEK = 7 * 86400.0

WORDS = [
    "alpha",
    "budget",
    "client",
    "delta",
    "engine",
    "finance",
    "growth",
    "harbor",
    "invoice",
    "journal",
    "kernel",
    "ledger",
    "margin",
    "network",
    "office",
    "payment",
    "quarter",
    "revenue",
    "shipping",
    "tariff",
    "update",
    "vendor",
    "warehouse",
    "yearly",
    "zone",
    "account",
    "balance",
    "contract",
    "deposit",
    "expense",
    "forecast",
    "general",
    "holding",
    "income",
]


def make_text(seed: int, n_words: int = 300) -> str:
    rng = random.Random(seed)
    return " ".join(rng.choice(WORDS) + str(rng.randint(0, 99)) for _ in range(n_words))


def edit_text(text: str, frac: float, seed: int) -> str:
    """Replace consecutive word blocks, leaving roughly (1 - frac) of the 5-shingles intact."""
    rng = random.Random(seed)
    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        if rng.random() < frac / 5:
            out.extend(f"edited{rng.randint(0, 9999)}" for _ in range(5))
            i += 5
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)


def make_image(path: Path, seed: int, size: tuple[int, int] = (640, 480)) -> None:
    rng = random.Random(seed)
    img = Image.new("RGB", size, (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)))
    d = ImageDraw.Draw(img)
    for _ in range(14):
        x0, y0 = rng.randint(0, size[0] - 40), rng.randint(0, size[1] - 40)
        x1, y1 = x0 + rng.randint(30, size[0] // 2), y0 + rng.randint(30, size[1] // 2)
        col = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        if rng.random() < 0.5:
            d.rectangle((x0, y0, x1, y1), fill=col)
        else:
            d.ellipse((x0, y0, x1, y1), fill=col)
    img.save(path, "PNG" if path.suffix == ".png" else "JPEG", quality=95)


def item(
    path: Path,
    side: Literal["input", "output"] = "input",
    *,
    text: str = "",
    is_image: bool = False,
    mtime: float = NOW,
    key: str | None = None,
) -> DedupItem:
    feats = compute_features(path, text, is_image, CFG)
    return DedupItem(
        key=key or f"{'f' if side == 'input' else 'ix'}:{path.name}",
        side=side,
        name=path.name,
        size=path.stat().st_size,
        mtime=mtime,
        ext=path.suffix.lstrip("."),
        quick_hash=feats.quick_hash,
        content_hash=content_hash(path),
        phash=feats.phash,
        dhash=feats.dhash,
        width=feats.width,
        height=feats.height,
        minhash=feats.minhash,
        text_len=feats.text_len,
    )


def write(path: Path, data: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def by_a(ms: list[DupMatch]) -> dict[str, DupMatch]:
    out = {m.a: m for m in ms}
    assert len(out) == len(ms), "more than one match for an input key"
    return out


# ------------------------------------------------------------------ exact


def test_exact_input_vs_output(tmp_path: Path) -> None:
    data = os.urandom(5000)
    a = item(write(tmp_path / "in" / "report (1).pdf", data))
    b = item(write(tmp_path / "out" / "report.pdf", data), "output")
    (m,) = find_duplicates([a, b], CFG)
    assert (m.a, m.b, m.relation, m.similarity) == (a.key, b.key, "exact", 1.0)
    assert m.keep == b.key
    assert m.reason == "Identical to report.pdf (100%)"


def test_same_size_and_quick_hash_but_different_content_is_not_exact(tmp_path: Path) -> None:
    # quick_hash only reads the first/last 64 KB; differ in the middle of a big file
    big = bytearray(os.urandom(300_000))
    other = bytearray(big)
    other[150_000] ^= 0xFF
    a = item(write(tmp_path / "a.bin", bytes(big)))
    b = item(write(tmp_path / "b.bin", bytes(other)))
    assert a.size == b.size and a.quick_hash == b.quick_hash
    assert a.content_hash != b.content_hash
    assert find_duplicates([a, b], CFG) == []


def test_exact_requires_content_hash(tmp_path: Path) -> None:
    data = os.urandom(100)
    a = item(write(tmp_path / "a.bin", data))
    b = item(write(tmp_path / "b.bin", data))
    a2 = a.model_copy(update={"content_hash": ""})
    assert find_duplicates([a2, b], CFG) == []


def test_input_input_yields_one_card(tmp_path: Path) -> None:
    data = os.urandom(2000)
    first = item(write(tmp_path / "x.bin", data))
    second = item(write(tmp_path / "x copy.bin", data))
    ms = find_duplicates([first, second], CFG)
    assert len(ms) == 1
    assert (ms[0].a, ms[0].b) == (second.key, first.key)  # a is the later item
    assert ms[0].keep == first.key  # cleaner name


def test_one_match_per_input_key(tmp_path: Path) -> None:
    data = os.urandom(2000)
    items = [
        item(write(tmp_path / "in" / "x.bin", data)),
        item(write(tmp_path / "in" / "x (1).bin", data)),
        item(write(tmp_path / "in" / "x (2).bin", data)),
        item(write(tmp_path / "out" / "x.bin", data), "output"),
    ]
    ms = by_a(find_duplicates(items, CFG))
    assert set(ms) == {i.key for i in items[:3]}
    assert all(m.b == "ix:x.bin" for m in ms.values())  # output copy is the best partner


def test_unrelated_files_no_match(tmp_path: Path) -> None:
    a = item(write(tmp_path / "a.bin", os.urandom(500)))
    b = item(write(tmp_path / "b.bin", os.urandom(500)))
    assert find_duplicates([a, b], CFG) == []


# ------------------------------------------------------------------ near image


def test_near_image_resized_recompressed(tmp_path: Path) -> None:
    big = tmp_path / "photo.png"
    make_image(big, seed=1)
    with Image.open(big) as im:
        im.resize((im.width // 2, im.height // 2), Image.Resampling.LANCZOS).save(
            tmp_path / "photo_small.jpg", "JPEG", quality=70
        )
    make_image(tmp_path / "other.png", seed=2)
    a = item(tmp_path / "photo_small.jpg", is_image=True)
    b = item(big, is_image=True)
    c = item(tmp_path / "other.png", is_image=True)
    assert a.phash and a.dhash and (a.width, a.height) == (320, 240)
    ms = find_duplicates([b, a, c], CFG)
    assert len(ms) == 1
    m = ms[0]
    assert (m.a, m.b, m.relation) == (a.key, b.key, "near_image")
    assert m.similarity > 0.9
    assert m.keep == b.key  # larger resolution
    assert "photo.png" in m.reason and "lower resolution" in m.reason and "%" in m.reason


def test_unrelated_images_not_matched(tmp_path: Path) -> None:
    for s in range(4):
        make_image(tmp_path / f"i{s}.png", seed=10 + s)
    items = [item(tmp_path / f"i{s}.png", is_image=True) for s in range(4)]
    assert find_duplicates(items, CFG) == []


# ------------------------------------------------------------------ near text / version


def test_near_text(tmp_path: Path) -> None:
    base = make_text(1)
    near = base + " one more short closing sentence added here"
    a = item(write(tmp_path / "memo.txt", base), text=base)
    b = item(write(tmp_path / "notes.txt", near), text=near)
    c = item(write(tmp_path / "unrelated.txt", make_text(2)), text=make_text(2))
    (m,) = find_duplicates([a, b, c], CFG)
    assert (m.a, m.b, m.relation) == (b.key, a.key, "near_text")
    assert m.similarity >= CFG.text_jaccard
    assert m.reason.endswith("same text as memo.txt") and "%" in m.reason


def test_version_of_by_name_and_partial_overlap(tmp_path: Path) -> None:
    base = make_text(3)
    edited = edit_text(base, 0.3, seed=5)
    old = item(write(tmp_path / "proposal.docx", "x"), text=base, mtime=NOW - 3 * WEEK)
    new = item(write(tmp_path / "proposal_v2.docx", "y"), text=edited, mtime=NOW)
    sim = jaccard(old.minhash, new.minhash)
    assert CFG.version_low <= sim < CFG.text_jaccard, sim
    ms = find_duplicates([old, new], CFG)
    (m,) = ms
    assert (m.a, m.b, m.relation) == (new.key, old.key, "version")
    assert m.keep == new.key  # newest
    assert "newer version of proposal.docx" in m.reason and "3 weeks" in m.reason
    # reversed order: the older doc is the card, "older version of ..."
    (m2,) = find_duplicates([new, old], CFG)
    assert (m2.a, m2.b, m2.keep) == (old.key, new.key, new.key)
    assert "older version of proposal_v2.docx" in m2.reason
    assert "modified 3 weeks earlier" in m2.reason


def test_partial_overlap_with_unrelated_names_is_not_a_version(tmp_path: Path) -> None:
    base = make_text(4)
    a = item(write(tmp_path / "taxes.docx", "x"), text=base)
    b = item(write(tmp_path / "vacation plan.docx", "y"), text=edit_text(base, 0.3, 6))
    assert find_duplicates([a, b], CFG) == []


def test_short_text_has_no_minhash(tmp_path: Path) -> None:
    p = write(tmp_path / "short.txt", "hello world")
    assert compute_features(p, "hello world", False, CFG).minhash == b""


# ------------------------------------------------------------------ recommendation rules


def test_recommend_output_copy_preferred_over_newer_input(tmp_path: Path) -> None:
    data = os.urandom(300)
    inp = item(write(tmp_path / "in.bin", data), mtime=NOW)
    out = item(write(tmp_path / "out.bin", data), "output", mtime=NOW - 10 * WEEK)
    (m,) = find_duplicates([inp, out], CFG)
    assert m.keep == out.key


def test_recommend_larger_resolution_even_if_older(tmp_path: Path) -> None:
    big = tmp_path / "pic.png"
    make_image(big, seed=7)
    with Image.open(big) as im:
        im.resize((320, 240)).save(tmp_path / "pic_small.png")
    a = item(big, is_image=True, mtime=NOW - WEEK)
    b = item(tmp_path / "pic_small.png", is_image=True, mtime=NOW)
    (m,) = find_duplicates([a, b], CFG)
    assert (m.a, m.keep) == (b.key, a.key)


def test_recommend_newest_then_cleaner_name(tmp_path: Path) -> None:
    data = os.urandom(300)
    old = item(write(tmp_path / "a.bin", data), mtime=NOW - 5 * WEEK)
    new = item(write(tmp_path / "b.bin", data), mtime=NOW)
    assert find_duplicates([old, new], CFG)[0].keep == new.key
    assert find_duplicates([new, old], CFG)[0].keep == new.key
    messy = item(write(tmp_path / "doc copy.bin", data), mtime=NOW)
    clean = item(write(tmp_path / "doc.bin", data), mtime=NOW)
    assert find_duplicates([messy, clean], CFG)[0].keep == clean.key
    assert find_duplicates([clean, messy], CFG)[0].keep == clean.key


def test_humanize_delta() -> None:
    assert humanize_delta(10) == "moments"
    assert humanize_delta(-3 * WEEK) == "3 weeks"
    assert humanize_delta(86400) == "1 day"
    assert humanize_delta(2 * 3600) == "2 hours"


def test_compute_features_never_raises(tmp_path: Path) -> None:
    f = compute_features(tmp_path / "missing.png", "", True, CFG)
    assert f.phash == "" and f.minhash == b""
    bad = write(tmp_path / "bad.png", b"not an image")
    assert compute_features(bad, "", True, CFG).phash == ""
    d = tmp_path / "bundle"
    d.mkdir()
    assert compute_features(d, "x" * 500, False, CFG).minhash == b""
