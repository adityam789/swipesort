"""LLM interface (M4): JSON-schema chat used for rerank and (M5) folder naming.

CONTRACT (implemented by the LLM agent):

class LLM(Protocol):
    def model_id(self) -> str: ...
    def chat_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...
        # raises swipesort.ml.gateway.ModelUnavailable when the runtime/model isn't available

def get_llm(settings: Settings) -> LLM | None
    # settings.llm.provider: "ollama" -> OllamaLLM (model = settings.llm.eco_model if
    # settings.llm.eco else settings.models.llm); "llamacpp" -> LlamaCppLLM (in-process,
    # optional dependency, GGUF from Hugging Face); "none" -> None.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from swipesort.config import Settings
from swipesort.ml.gateway import ModelUnavailable

LLM_SEED = 7
OLLAMA_KEEP_ALIVE = "30m"


class LLM(Protocol):
    def model_id(self) -> str: ...

    def chat_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


class OllamaLLM:
    """Ollama /api/chat with a JSON-schema `format`; deterministic (temperature 0, fixed seed)."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        from swipesort.ml.providers.ollama import OllamaProvider

        self.model = settings.models.llm if not settings.llm.eco else settings.llm.eco_model
        client = httpx.Client(
            base_url=settings.models.ollama_url,
            timeout=settings.llm.timeout_s,
            transport=transport,
        )
        self._http = OllamaProvider(settings, client=client)

    def model_id(self) -> str:
        return f"ollama:{self.model}"

    def chat_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        import json

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "format": schema,
            "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": {"temperature": 0, "seed": LLM_SEED},
        }
        data = self._http._post("/api/chat", payload, self.model)
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


def get_llm(settings: Settings, transport: httpx.BaseTransport | None = None) -> LLM | None:
    name = settings.llm.provider
    if name == "none":
        return None
    if name == "ollama":
        return OllamaLLM(settings, transport=transport)
    if name == "llamacpp":
        from swipesort.ml.providers.llamacpp import LlamaCppLLM

        return LlamaCppLLM(settings)
    raise ValueError(f"Unknown LLM provider: {name!r} (expected 'ollama', 'llamacpp' or 'none')")
