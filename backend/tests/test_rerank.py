from __future__ import annotations

import json
import sys
import types
from typing import Any

import httpx
import pytest

from swipesort.config import Settings
from swipesort.ml import ModelUnavailable
from swipesort.ml.llm import OllamaLLM, get_llm
from swipesort.ml.providers.llamacpp import LlamaCppLLM
from swipesort.ml.rerank import (
    RERANK_SCHEMA,
    RerankResult,
    build_prompt,
    combine,
    parse,
    rerank,
    should_rerank,
)

CANDS = ["Finance/Invoices", "Finance/Taxes/2025", "Travel", "Photos/Family"]


def test_prompt_lists_all_candidates_and_truncates() -> None:
    p = build_prompt("inv.pdf", "x" * 5000, CANDS, 100)
    for i, c in enumerate(CANDS, 1):
        assert f"{i}. {c}" in p
    assert "inv.pdf" in p and "x" * 100 in p and "x" * 101 not in p
    assert "JSON" in p


def test_parse_tolerance() -> None:
    assert parse({"choice": " finance/invoices/ ", "alternates": [], "reason": "r"}, CANDS)
    r = parse(
        {"choice": "2", "alternates": [1, "TRAVEL", "Nope", "Finance/Taxes/2025", 9],
         "new_folder_suggestion": "  ", "reason": "  An  invoice\n"},
        CANDS,
    )
    assert r is not None
    assert r.choice == "Finance/Taxes/2025"
    assert r.alternates == ["Finance/Invoices", "Travel"]
    assert r.new_folder_suggestion is None and r.reason == "An invoice"
    assert parse({"choice": 3}, CANDS) is not None
    assert parse({"choice": "Elsewhere"}, CANDS) is None
    assert parse({"choice": 0}, CANDS) is None
    assert parse({"choice": True}, CANDS) is None
    assert parse({}, CANDS) is None


def test_combine() -> None:
    s = Settings()
    agree = RerankResult(choice="Finance/Invoices/")
    dis = RerankResult(choice="Travel")
    # agree: boost + stays confirm
    c, t, a = combine("Finance/Invoices", 0.92, "confirm", agree, s.llm, s.tiers)
    assert (round(c, 2), t, a) == (0.97, "confirm", True)
    assert combine("Finance/Invoices", 0.99, "confirm", agree, s.llm, s.tiers)[0] == 1.0
    # agree: boost lifts inbox -> choices, but never grants confirm on its own
    assert combine("Finance/Invoices", 0.57, "inbox", agree, s.llm, s.tiers)[1] == "choices"
    assert combine("Finance/Invoices", 0.88, "choices", agree, s.llm, s.tiers)[1] == "choices"
    # disagree: capped below confirm
    c, t, a = combine("Finance/Invoices", 0.97, "confirm", dis, s.llm, s.tiers)
    assert (c, t, a) == (s.llm.disagree_cap, "choices", False)
    c, t, a = combine("Finance/Invoices", 0.5, "inbox", dis, s.llm, s.tiers)
    assert (c, t, a) == (0.5, "inbox", False)
    assert combine("Finance/Invoices", 0.7, "choices", dis, s.llm, s.tiers)[0] == 0.7
    # no rerank: unchanged
    assert combine("Finance/Invoices", 0.8, "choices", None, s.llm, s.tiers) == (
        0.8, "choices", None,
    )


def test_should_rerank_eco() -> None:
    s = Settings()
    assert should_rerank("confirm", s.llm) and should_rerank("choices", s.llm)
    assert not should_rerank("inbox", s.llm)
    s.llm.eco = True
    assert should_rerank("choices", s.llm) and not should_rerank("confirm", s.llm)


class StubLLM:
    def __init__(self, out: dict[str, Any] | Exception) -> None:
        self.out = out
        self.prompts: list[str] = []

    def model_id(self) -> str:
        return "stub"

    def chat_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        if isinstance(self.out, Exception):
            raise self.out
        return self.out


def test_rerank_function() -> None:
    s = Settings()
    llm = StubLLM({"choice": "travel", "alternates": [], "reason": "Trip"})
    r = rerank(llm, "a.pdf", "digest", CANDS, s.llm)
    assert r is not None and r.choice == "Travel" and "Finance/Taxes/2025" in llm.prompts[0]
    assert rerank(StubLLM(ValueError("x")), "a", "d", CANDS, s.llm) is None
    assert rerank(StubLLM({"choice": "zzz"}), "a", "d", CANDS, s.llm) is None
    with pytest.raises(ModelUnavailable):
        rerank(StubLLM(ModelUnavailable("down")), "a", "d", CANDS, s.llm)


def test_get_llm_selection() -> None:
    s = Settings()
    s.llm.provider = "none"
    assert get_llm(s) is None
    s.llm.provider = "ollama"
    llm = get_llm(s)
    assert isinstance(llm, OllamaLLM) and llm.model_id() == f"ollama:{s.models.llm}"
    s.llm.eco = True
    llm = get_llm(s)
    assert llm is not None and llm.model_id() == f"ollama:{s.llm.eco_model}"
    s.llm.provider = "llamacpp"
    assert isinstance(get_llm(s), LlamaCppLLM)
    s.llm.provider = "bogus"
    with pytest.raises(ValueError):
        get_llm(s)


def test_ollama_request_body_and_response() -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/chat"
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": '{"choice": "Travel"}'}})

    s = Settings()
    llm = OllamaLLM(s, transport=httpx.MockTransport(handler))
    assert llm.chat_json("hi", RERANK_SCHEMA) == {"choice": "Travel"}
    body = seen[0]
    assert body["format"] == RERANK_SCHEMA and body["stream"] is False
    assert body["options"]["temperature"] == 0 and "seed" in body["options"]
    assert body["keep_alive"] and body["model"] == s.models.llm


@pytest.mark.parametrize("status", [404, 500])
def test_ollama_errors_are_model_unavailable(status: int) -> None:
    llm = OllamaLLM(
        Settings(), transport=httpx.MockTransport(lambda r: httpx.Response(status, text="no"))
    )
    with pytest.raises(ModelUnavailable):
        llm.chat_json("hi", RERANK_SCHEMA)

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ModelUnavailable):
        OllamaLLM(Settings(), transport=httpx.MockTransport(boom)).chat_json("hi", {})


def test_llamacpp_missing_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "llama_cpp", None)  # makes `import llama_cpp` raise
    with pytest.raises(ModelUnavailable, match="llm-inprocess"):
        LlamaCppLLM(Settings()).chat_json("hi", {})


def test_llamacpp_mocked_happy_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("SWIPESORT_HOME", str(tmp_path))
    calls: dict[str, Any] = {"loads": 0}

    class FakeLlama:
        @classmethod
        def from_pretrained(cls, **kw: Any) -> FakeLlama:
            calls["loads"] += 1
            calls["load_kw"] = kw
            return cls()

        def create_chat_completion(self, **kw: Any) -> dict[str, Any]:
            calls["chat_kw"] = kw
            return {"choices": [{"message": {"content": '{"choice": "Travel"}'}}]}

    mod = types.ModuleType("llama_cpp")
    mod.Llama = FakeLlama  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "llama_cpp", mod)
    s = Settings()
    llm = LlamaCppLLM(s)
    assert llm.chat_json("hi", RERANK_SCHEMA) == {"choice": "Travel"}
    assert llm.chat_json("again", RERANK_SCHEMA) == {"choice": "Travel"}
    assert calls["loads"] == 1
    kw = calls["load_kw"]
    assert kw["repo_id"] == s.llm.llamacpp_repo and kw["filename"] == s.llm.llamacpp_file
    assert kw["n_ctx"] == s.llm.llamacpp_ctx and kw["verbose"] is False
    assert str(tmp_path / "models") in str(kw["cache_dir"])
    ck = calls["chat_kw"]
    assert ck["temperature"] == 0
    assert ck["response_format"] == {"type": "json_object", "schema": RERANK_SCHEMA}
