"""Duplicate & redundancy detection. Pure functions over precomputed features.

CONTRACT (implemented by the dedup agent; used by pipeline/worker.py):

class DedupItem(BaseModel):
    key: str                 # "f:<file id>" (input) or "ix:<indexed file id>" (output)
    side: Literal["input", "output"]
    name: str; size: int; mtime: float; ext: str
    quick_hash: str; content_hash: str
    phash: str = ""; dhash: str = ""; width: int = 0; height: int = 0
    minhash: bytes = b""; text_len: int = 0

class DupMatch(BaseModel):
    a: str                   # key of the INPUT file the card is about
    b: str                   # key of the other copy (input or output)
    relation: Literal["exact", "near_image", "near_text", "version"]
    similarity: float
    keep: str                # key recommended to keep (a or b)
    reason: str

def find_duplicates(items: list[DedupItem], cfg: Dedup) -> list[DupMatch]
    # input-vs-output and input-vs-input. At most ONE match per input key `a` (best by
    # relation rank exact > near_image > near_text > version, then similarity).
    # For an input-input pair, only one card: `a` is the one later in sorted rel order... the
    # caller passes items in card order; `a` = the later item, `b` = the earlier.
    # Exact: size equal -> quick_hash equal -> content_hash equal (never on quick hash alone).
    # Recommendation: prefer the copy already in the output tree; else larger resolution
    # (images); else newest mtime; else the one with the shorter/cleaner name (no "copy", "(1)").
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from swipesort.config import Dedup

Relation = Literal["exact", "near_image", "near_text", "version"]

_RANK: dict[str, int] = {"exact": 0, "near_image": 1, "near_text": 2, "version": 3}
_MTIME_TOLERANCE_S = 2.0  # FAT/exFAT mtime granularity


class DedupItem(BaseModel):
    key: str  # "f:<file id>" (input) or "ix:<indexed file id>" (output)
    side: Literal["input", "output"]
    name: str
    size: int
    mtime: float
    ext: str
    quick_hash: str
    content_hash: str
    phash: str = ""
    dhash: str = ""
    width: int = 0
    height: int = 0
    minhash: bytes = b""
    text_len: int = 0


class DupMatch(BaseModel):
    a: str  # key of the INPUT file the card is about
    b: str  # key of the other copy (input or output)
    relation: Relation
    similarity: float
    keep: str  # key recommended to keep (a or b)
    reason: str


# --------------------------------------------------------------------------- helpers

_SEP = re.compile(r"[_\-.\s]+")
_PAREN_NUM = re.compile(r"\(\d+\)")
_COPY_OF = re.compile(r"^copy of ")
_NOISE_TOKEN = re.compile(r"^(?:v\d+|final\d*|edited|draft|copy)$")
_DIRTY_NAME = re.compile(
    r"\(\d+\)|\bcopy\b|(?<![a-z0-9])v\d+(?![a-z0-9])|\bfinal\b|\bedited\b|\bdraft\b",
    re.IGNORECASE,
)


def version_stem(name: str) -> str:
    """Filename stem with version/copy markers removed (dates and other digits are kept)."""
    stem = name.rsplit(".", 1)[0] if "." in name.strip(".") else name
    s = _PAREN_NUM.sub(" ", stem.lower())
    s = _COPY_OF.sub("", s.strip())
    tokens = [t for t in _SEP.split(s) if t and not _NOISE_TOKEN.match(t)]
    return " ".join(tokens)


def stems_similar(a: str, b: str, threshold: float) -> bool:
    """Version-normalized stems are similar, carry the same numbers (L5 is not L1) and the same
    words (``summary_Mar`` is not ``summary_Jun``; ``Soylent mortgage`` is not ``Cyberdyne
    mortgage``): names may differ only by version markers, separators and case."""
    sa, sb = version_stem(a), version_stem(b)
    if not sa or not sb:
        return False
    if [t for t in sa.split() if any(c.isdigit() for c in t)] != [
        t for t in sb.split() if any(c.isdigit() for c in t)
    ]:
        return False
    words_a = {t for t in sa.split() if t.isalpha() and len(t) >= 3}
    words_b = {t for t in sb.split() if t.isalpha() and len(t) >= 3}
    if words_a != words_b:
        return False
    return SequenceMatcher(None, sa, sb).ratio() >= threshold


def humanize_delta(seconds: float) -> str:
    s = abs(seconds)
    if s < 60:
        return "moments"
    units = (
        (365 * 86400, "year"),
        (30 * 86400, "month"),
        (7 * 86400, "week"),
        (86400, "day"),
        (3600, "hour"),
        (60, "minute"),
    )
    for span, label in units:
        if s >= span:
            n = int(s // span)
            return f"{n} {label}{'' if n == 1 else 's'}"
    return "moments"  # pragma: no cover


def _pct(sim: float, exact: bool = False) -> int:
    return 100 if exact else min(99, round(sim * 100))


def _pixels(it: DedupItem) -> int:
    return it.width * it.height


def _dirtiness(name: str) -> int:
    return len(_DIRTY_NAME.findall(name))


def _recommend(a: DedupItem, b: DedupItem) -> str:
    """Key of the copy to keep: output tree, larger resolution, newest mtime, cleaner name."""
    if a.side != b.side:
        return a.key if a.side == "output" else b.key
    pa, pb = _pixels(a), _pixels(b)
    if pa and pb and pa != pb:
        return a.key if pa > pb else b.key
    if abs(a.mtime - b.mtime) > _MTIME_TOLERANCE_S:
        return a.key if a.mtime > b.mtime else b.key
    da, db = _dirtiness(a.name), _dirtiness(b.name)
    if da != db:
        return a.key if da < db else b.key
    if len(a.name) != len(b.name):
        return a.key if len(a.name) < len(b.name) else b.key
    return b.key


def _hex_to_u64(h: str) -> int | None:
    if len(h) != 16:
        return None
    try:
        return int(h, 16)
    except ValueError:
        return None


_POP = np.array([i.bit_count() for i in range(256)], dtype=np.uint8)


def _hamming(arr: npt.NDArray[np.uint64], ref: np.uint64) -> npt.NDArray[np.int64]:
    x = np.ascontiguousarray(arr ^ ref)
    return _POP[x.view(np.uint8).reshape(-1, 8)].sum(axis=1, dtype=np.int64)


def _reason(rel: Relation, a: DedupItem, b: DedupItem, sim: float) -> str:
    if rel == "exact":
        return f"Identical to {b.name} (100%)"
    if rel == "near_image":
        pa, pb = _pixels(a), _pixels(b)
        how = ""
        if pa and pb and pa < pb:
            how = ", lower resolution"
        elif pa and pb and pa > pb:
            how = ", higher resolution"
        return f"Same photo as {b.name}{how} ({_pct(sim)}%)"
    if rel == "near_text":
        return f"{_pct(sim)}% same text as {b.name}"
    delta = a.mtime - b.mtime
    if abs(delta) <= _MTIME_TOLERANCE_S:
        return f"Looks like another version of {b.name} ({_pct(sim)}% same text)"
    if delta < 0:
        return f"Looks like an older version of {b.name} (modified {humanize_delta(delta)} earlier)"
    return f"Looks like a newer version of {b.name} (modified {humanize_delta(delta)} later)"


# --------------------------------------------------------------------------- main


def find_duplicates(items: list[DedupItem], cfg: Dedup) -> list[DupMatch]:
    """See the module docstring for the contract."""
    n = len(items)
    if n < 2:
        return []

    # exact buckets: size -> quick_hash -> content_hash -> [indices]
    exact: dict[int, dict[str, dict[str, list[int]]]] = {}
    for idx, it in enumerate(items):
        if it.quick_hash and it.content_hash:
            exact.setdefault(it.size, {}).setdefault(it.quick_hash, {}).setdefault(
                it.content_hash, []
            ).append(idx)

    # image features
    ph = np.zeros(n, dtype=np.uint64)
    dh = np.zeros(n, dtype=np.uint64)
    has_img = np.zeros(n, dtype=bool)
    for idx, it in enumerate(items):
        p, d = _hex_to_u64(it.phash), _hex_to_u64(it.dhash)
        if p is not None and d is not None:
            ph[idx], dh[idx], has_img[idx] = p, d, True

    # text features
    nbytes = cfg.minhash_perm * 8
    has_txt = np.array([len(it.minhash) == nbytes for it in items], dtype=bool)
    mh = np.zeros((n, cfg.minhash_perm), dtype=np.uint64)
    for k in np.flatnonzero(has_txt).tolist():
        mh[k] = np.frombuffer(items[k].minhash, dtype="<u8")

    outputs = np.array([it.side == "output" for it in items], dtype=bool)
    idx_arr = np.arange(n)

    def order(j: int) -> tuple[int, int]:  # tie-break: output copy first, then earliest
        return (0 if items[j].side == "output" else 1, j)

    matches: list[DupMatch] = []
    for i, a in enumerate(items):
        if a.side != "input":
            continue
        allowed = (idx_arr != i) & (outputs | (idx_arr < i))
        best: tuple[int, float, tuple[int, int], int, Relation] | None = None

        def offer(rel: Relation, sim: float, j: int) -> None:
            nonlocal best
            cand = (_RANK[rel], -sim, order(j), j, rel)
            if best is None or cand[:3] < best[:3]:
                best = cand

        # exact
        if a.quick_hash and a.content_hash:
            same = exact.get(a.size, {}).get(a.quick_hash, {}).get(a.content_hash, [])
            for j in same:
                if allowed[j]:
                    offer("exact", 1.0, j)

        # near image
        if has_img[i]:
            cand_mask = allowed & has_img
            if cand_mask.any():
                dp = _hamming(ph, ph[i])
                dd = _hamming(dh, dh[i])
                ok = cand_mask & (dp <= cfg.image_hamming) & (dd <= cfg.image_hamming)
                for j in np.flatnonzero(ok).tolist():
                    sim = 1.0 - (float(dp[j]) + float(dd[j])) / 2.0 / 64.0
                    offer("near_image", sim, j)

        # near text / version
        if has_txt[i]:
            cand_mask = allowed & has_txt
            if cand_mask.any():
                sims = (mh == mh[i]).mean(axis=1)
                ok = cand_mask & (sims >= cfg.version_low)
                for j in np.flatnonzero(ok).tolist():
                    s = float(sims[j])
                    if s >= cfg.text_jaccard:
                        offer("near_text", s, j)
                    elif stems_similar(a.name, items[j].name, cfg.stem_similarity):
                        offer("version", s, j)

        if best is not None:
            _, neg_sim, _, j, rel = best
            other = items[j]
            sim = -neg_sim
            matches.append(
                DupMatch(
                    a=a.key,
                    b=other.key,
                    relation=rel,
                    similarity=sim,
                    keep=_recommend(a, other),
                    reason=_reason(rel, a, other, sim),
                )
            )
    return matches
