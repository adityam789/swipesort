"""In-process embeddings via fastembed (ONNX Runtime, no PyTorch) + Ollama for generative calls.

Embeddings are the only model M1 needs, so the app works with zero setup: the embedding model
(Hugging Face, ~130 MB) downloads into ~/.swipesort/models on first use. Captioning and the
LLM (M4+) are delegated to Ollama when it's running and degrade to ModelUnavailable otherwise.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from swipesort.config import Settings, app_dir
from swipesort.ml.gateway import ModelUnavailable


class LocalProvider:
    name = "local"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model: Any = None
        self._load_lock = threading.Lock()
        self._ollama: Any = None

    def embed_model_id(self) -> str:
        return f"local:{self.settings.models.local_embed}"

    def _embedder(self) -> Any:
        with self._load_lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding
                except ImportError as e:
                    raise ModelUnavailable("fastembed is not installed") from e
                cache = app_dir() / "models"
                cache.mkdir(parents=True, exist_ok=True)
                try:
                    self._model = TextEmbedding(model_name=self.settings.models.local_embed,
                                                cache_dir=str(cache))
                except Exception as e:
                    raise ModelUnavailable(
                        f"could not load embedding model {self.settings.models.local_embed}: {e}"
                    ) from e
            return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._embedder()
        out: list[list[float]] = []
        for v in model.embed(texts, batch_size=self.settings.pipeline.embed_batch):
            arr = np.asarray(v, dtype=np.float32)
            n = float(np.linalg.norm(arr))
            out.append((arr / n if n > 0 else arr).tolist())
        return out

    def _gen(self) -> Any:
        if self._ollama is None:
            from swipesort.ml.providers.ollama import OllamaProvider

            self._ollama = OllamaProvider(self.settings)
        return self._ollama

    def caption(self, image: Path) -> str:
        result: str = self._gen().caption(image)
        return result

    def chat_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = self._gen().chat_json(prompt, schema)
        return result
