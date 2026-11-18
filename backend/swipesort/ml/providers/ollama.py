"""Ollama provider (local models). All model names come from Settings.models."""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from typing import Any

import httpx

from swipesort.config import Settings
from swipesort.ml.gateway import ModelUnavailable

_CAPTION_PROMPT = "Describe this image in one or two short sentences."


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm > 0 else vec


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.cfg = settings.models
        self._client = client or httpx.Client(
            base_url=self.cfg.ollama_url,
            timeout=self.cfg.request_timeout_s,
            transport=transport,
        )

    def embed_model_id(self) -> str:
        return f"ollama:{self.cfg.embed}"

    def _post(self, path: str, payload: dict[str, Any], model: str) -> dict[str, Any]:
        try:
            resp = self._client.post(path, json=payload)
        except httpx.TransportError as e:  # connect errors, timeouts, protocol errors
            raise ModelUnavailable(
                f"Cannot reach Ollama at {self.cfg.ollama_url} ({e.__class__.__name__}). "
                "Is `ollama serve` running?"
            ) from e
        if resp.status_code == 404:
            raise ModelUnavailable(f"Model '{model}' not found. Run `ollama pull {model}`.")
        if resp.status_code >= 400:
            raise ModelUnavailable(
                f"Ollama returned HTTP {resp.status_code} for {path}: {resp.text[:200]}"
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise ModelUnavailable(f"Ollama returned invalid JSON for {path}") from e
        if not isinstance(data, dict):
            raise ModelUnavailable(f"Unexpected Ollama response for {path}")
        return data

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        data = self._post("/api/embed", {"model": self.cfg.embed, "input": texts}, self.cfg.embed)
        embs = data.get("embeddings")
        if not isinstance(embs, list) or len(embs) != len(texts):
            raise ModelUnavailable("Ollama /api/embed returned an unexpected number of vectors")
        return [_normalize([float(x) for x in e]) for e in embs]

    def caption(self, image: Path) -> str:
        b64 = base64.b64encode(image.read_bytes()).decode("ascii")
        payload = {
            "model": self.cfg.vlm,
            "prompt": _CAPTION_PROMPT,
            "images": [b64],
            "stream": False,
        }
        data = self._post("/api/generate", payload, self.cfg.vlm)
        return str(data.get("response", "")).strip()

    def chat_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.cfg.llm,
            "messages": [{"role": "user", "content": prompt}],
            "format": schema,
            "stream": False,
        }
        data = self._post("/api/chat", payload, self.cfg.llm)
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ModelUnavailable("Ollama /api/chat response had no message content")
        try:
            out = json.loads(content)
        except ValueError as e:
            raise ModelUnavailable("LLM returned invalid JSON") from e
        if not isinstance(out, dict):
            raise ModelUnavailable("LLM returned JSON that is not an object")
        return out
