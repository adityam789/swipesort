"""Model gateway package. `get_provider` picks the provider named in settings."""

from __future__ import annotations

from swipesort.config import Settings
from swipesort.ml.gateway import ModelUnavailable, Provider

__all__ = ["ModelUnavailable", "Provider", "get_provider"]


def get_provider(settings: Settings) -> Provider:
    name = settings.models.provider
    if name == "fake":
        from swipesort.ml.providers.fake import FakeProvider

        return FakeProvider(settings)
    if name == "ollama":
        from swipesort.ml.providers.ollama import OllamaProvider

        return OllamaProvider(settings)
    raise ValueError(f"Unknown model provider: {name!r} (expected 'ollama' or 'fake')")
