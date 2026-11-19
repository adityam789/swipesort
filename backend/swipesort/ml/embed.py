"""Embedding helpers: float32 packing and a DB-backed cache with a single-flight model lock."""

from __future__ import annotations

import threading

import numpy as np
import numpy.typing as npt
from sqlmodel import Session, select

from swipesort.db.models import Embedding
from swipesort.ml.gateway import Provider

# single-flight model queue. All provider calls go through this lock.
_model_lock = threading.Lock()


def pack(vec: npt.ArrayLike) -> bytes:
    return np.asarray(vec, dtype="<f4").tobytes()


def unpack(b: bytes) -> npt.NDArray[np.float32]:
    return np.frombuffer(b, dtype="<f4").astype(np.float32)


def embed_cached(
    db: Session,
    provider: Provider,
    items: list[tuple[str, str]],
    batch: int,
) -> dict[str, npt.NDArray[np.float32]]:
    """items = (cache_key, text). Returns {cache_key: vec}; misses are embedded and stored."""
    model_id = provider.embed_model_id()
    texts: dict[str, str] = {}
    for key, text in items:
        texts.setdefault(key, text)
    out: dict[str, npt.NDArray[np.float32]] = {}
    keys = list(texts)
    for i in range(0, len(keys), 500):  # stay under SQLite's variable limit
        chunk = keys[i : i + 500]
        rows = db.exec(
            select(Embedding).where(Embedding.model == model_id).where(Embedding.key.in_(chunk))  # type: ignore[attr-defined]
        ).all()
        for row in rows:
            out[row.key] = unpack(row.vec)
    misses = [k for k in keys if k not in out]
    step = max(1, batch)
    for i in range(0, len(misses), step):
        chunk = misses[i : i + step]
        with _model_lock:
            vecs = provider.embed([texts[k] for k in chunk])
        for k, v in zip(chunk, vecs, strict=True):
            arr = np.asarray(v, dtype=np.float32)
            out[k] = arr
            db.merge(Embedding(key=k, model=model_id, dim=int(arr.shape[0]), vec=pack(arr)))
        db.commit()
    return out
