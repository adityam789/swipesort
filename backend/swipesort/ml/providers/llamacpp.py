"""In-process llama.cpp LLM (optional extra: `uv sync --extra llm-inprocess`)."""

from __future__ import annotations

import json
import threading
from typing import Any

from swipesort.config import Settings, app_dir
from swipesort.ml.gateway import ModelUnavailable

INSTALL_HINT = "llama-cpp-python is not installed. Run `uv sync --extra llm-inprocess`."


class LlamaCppLLM:
    def __init__(self, settings: Settings) -> None:
        self.cfg = settings.llm
        self._lock = threading.Lock()
        self._llm: Any = None

    def model_id(self) -> str:
        return f"llamacpp:{self.cfg.llamacpp_repo}/{self.cfg.llamacpp_file}"

    def _load(self) -> Any:
        if self._llm is not None:
            return self._llm
        try:
            from llama_cpp import Llama  # type: ignore[import-not-found,unused-ignore]
        except ImportError as e:
            raise ModelUnavailable(INSTALL_HINT) from e
        cache = app_dir() / "models"
        try:
            cache.mkdir(parents=True, exist_ok=True)
            self._llm = Llama.from_pretrained(
                repo_id=self.cfg.llamacpp_repo,
                filename=self.cfg.llamacpp_file,
                n_ctx=self.cfg.llamacpp_ctx,
                verbose=False,
                cache_dir=str(cache),
            )
        except Exception as e:
            raise ModelUnavailable(
                f"Could not load llama.cpp model {self.cfg.llamacpp_repo}: {e}"
            ) from e
        return self._llm

    def chat_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            llm = self._load()
            try:
                res = llm.create_chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object", "schema": schema},
                    temperature=0,
                )
                content = res["choices"][0]["message"]["content"]
                out = json.loads(content)
            except (KeyError, IndexError, TypeError, ValueError) as e:
                raise ModelUnavailable(f"llama.cpp returned an unusable response: {e}") from e
            except Exception as e:
                raise ModelUnavailable(f"llama.cpp inference failed: {e}") from e
        if not isinstance(out, dict):
            raise ModelUnavailable("LLM returned JSON that is not an object")
        return out
