"""Model gateway. All model names come from config. Providers: ollama | fake."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class ModelUnavailable(Exception):
    """Provider not reachable / model not pulled. Callers degrade gracefully."""


class Provider(Protocol):
    name: str

    def embed_model_id(self) -> str: ...  # stable id for the cache key, e.g. "ollama:nomic-embed-text"
    def embed(self, texts: list[str]) -> list[list[float]]: ...  # L2-normalized vectors
    def caption(self, image: Path) -> str: ...  # "" if unsupported
    def chat_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...  # M4
