"""Dedup features: perceptual image hashes and text MinHash.

`compute_features` is a top-level function so it pickles into a ProcessPoolExecutor.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

import imagehash
import numpy as np
from datasketch import MinHash  # type: ignore[import-untyped,unused-ignore]
from PIL import Image, ImageOps
from pydantic import BaseModel

from swipesort.config import Dedup
from swipesort.pipeline.fingerprint import quick_hash

with contextlib.suppress(Exception):  # HEIC/HEIF support is optional at runtime
    from pillow_heif import register_heif_opener  # type: ignore[import-untyped,unused-ignore]

    register_heif_opener()

_WS = re.compile(r"\s+")
_HASH_SIZE = 8


class FeatureResult(BaseModel):
    """Mirrors db.models.Features minus content_hash."""

    quick_hash: str = ""
    phash: str = ""
    dhash: str = ""
    width: int = 0
    height: int = 0
    minhash: bytes = b""  # uint64 little-endian hashvalues
    text_len: int = 0


def normalize_text(text: str) -> str:
    return _WS.sub(" ", text.lower()).strip()


def _shingles(words: list[str], k: int) -> list[str]:
    if len(words) <= k:
        return [" ".join(words)]
    return [" ".join(words[i : i + k]) for i in range(len(words) - k + 1)]


def _minhash_bytes(norm: str, cfg: Dedup) -> bytes:
    mh = MinHash(num_perm=cfg.minhash_perm)
    for sh in _shingles(norm.split(" "), max(1, cfg.shingle)):
        mh.update(sh.encode("utf-8"))
    return np.asarray(mh.hashvalues, dtype="<u8").tobytes()


def minhash_from_bytes(b: bytes, num_perm: int) -> MinHash:
    """Rebuild a datasketch MinHash from stored uint64 LE hashvalues."""
    hv = np.frombuffer(b, dtype="<u8").astype(np.uint64)
    return MinHash(num_perm=num_perm, hashvalues=hv)


def jaccard(b1: bytes, b2: bytes) -> float:
    """Estimated Jaccard similarity of two stored MinHash signatures (0.0 if incomparable)."""
    if not b1 or len(b1) != len(b2):
        return 0.0
    a = np.frombuffer(b1, dtype="<u8")
    b = np.frombuffer(b2, dtype="<u8")
    return float(np.mean(a == b))


def _image_features(path: Path) -> tuple[str, str, int, int]:
    with Image.open(path) as raw:
        im = ImageOps.exif_transpose(raw)
        w, h = im.size
        gray = im.convert("L")
        ph = str(imagehash.phash(gray, hash_size=_HASH_SIZE))
        dh = str(imagehash.dhash(gray, hash_size=_HASH_SIZE))
    return ph, dh, int(w), int(h)


def compute_features(path: Path, text: str, is_image: bool, cfg: Dedup) -> FeatureResult:
    """Compute dedup features for one file. Never raises; failures yield empty fields."""
    res = FeatureResult()
    path = Path(path)
    try:
        if path.is_dir():
            return res  # bundles: exact hashing only
    except OSError:
        return res
    with contextlib.suppress(Exception):
        res.quick_hash = quick_hash(path)
    if is_image:
        try:
            res.phash, res.dhash, res.width, res.height = _image_features(path)
        except Exception:  # noqa: BLE001
            res.phash = res.dhash = ""
            res.width = res.height = 0
        return res
    try:
        norm = normalize_text(text or "")
        if len(norm) >= cfg.min_text_chars:
            res.minhash = _minhash_bytes(norm, cfg)
            res.text_len = len(norm)
    except Exception:  # noqa: BLE001
        res.minhash = b""
        res.text_len = 0
    return res
