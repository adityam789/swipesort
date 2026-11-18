"""Deterministic offline provider: signed feature hashing of word/bigram/char-4gram features.

No network, no model files. Texts that share vocabulary get high cosine similarity, which makes
tests and offline evaluation meaningful.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from swipesort.config import Settings

_WORD_RE = re.compile(r"[a-z0-9]+")
_CHAR_N = 4


def _features(text: str) -> Counter[str]:
    words = _WORD_RE.findall(text.lower())
    feats: Counter[str] = Counter()
    for i, w in enumerate(words):
        feats["u:" + w] += 1
        if i + 1 < len(words):
            feats[f"b:{w} {words[i + 1]}"] += 1
        for j in range(len(w) - _CHAR_N + 1):
            feats["c:" + w[j : j + _CHAR_N]] += 1
    return feats


@lru_cache(maxsize=65536)
def _hash(feature: str) -> int:
    return int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "little")


class FakeProvider:
    name = "fake"

    def __init__(self, settings: Settings) -> None:
        self.dim = max(8, settings.models.fake_dim)

    def embed_model_id(self) -> str:
        return f"fake:hash-{self.dim}"

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for feat, count in _features(text).items():
            h = _hash(feat)
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[h % self.dim] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def caption(self, image: Path) -> str:
        return ""

    def chat_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {}
