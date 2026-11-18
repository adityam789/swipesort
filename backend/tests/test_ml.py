from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from sqlmodel import Session

from swipesort.config import Settings
from swipesort.db.session import make_engine
from swipesort.ml import ModelUnavailable, get_provider
from swipesort.ml.embed import embed_cached, pack, unpack
from swipesort.ml.providers.fake import FakeProvider
from swipesort.ml.providers.ollama import OllamaProvider


def fake_settings(dim: int = 256) -> Settings:
    s = Settings()
    s.models.provider = "fake"
    s.models.fake_dim = dim
    return s


def cos(a: list[float], b: list[float]) -> float:
    return float(np.dot(a, b))


def test_fake_deterministic_normalized() -> None:
    p = FakeProvider(fake_settings(128))
    a = p.embed(["Invoice 2024 total due"])[0]
    b = p.embed(["Invoice 2024 total due"])[0]
    assert a == b and len(a) == 128
    assert abs(np.linalg.norm(a) - 1.0) < 1e-6
    assert p.embed_model_id() == "fake:hash-128"
    assert p.caption(Path("x.png")) == ""
    assert p.chat_json("hi", {}) == {}


def test_fake_semantic_similarity() -> None:
    p = FakeProvider(fake_settings())
    inv1, inv2, cook = p.embed(
        ["invoice payment due total amount", "payment invoice amount overdue", "pasta recipe oven"]
    )
    assert cos(inv1, inv2) > cos(inv1, cook) + 0.3
    assert p.embed([""])[0] == [0.0] * 256


def test_get_provider() -> None:
    assert isinstance(get_provider(fake_settings()), FakeProvider)
    assert isinstance(get_provider(Settings()), OllamaProvider)
    s = Settings()
    s.models.provider = "nope"
    with pytest.raises(ValueError):
        get_provider(s)


def test_pack_roundtrip() -> None:
    v = np.array([0.5, -1.25, 3.0], dtype=np.float32)
    b = pack(v)
    assert len(b) == 12
    assert np.array_equal(unpack(b), v)


def test_embed_cached_hits_and_batches() -> None:
    eng = make_engine(":memory:")
    prov = FakeProvider(fake_settings(64))
    calls: list[int] = []
    orig = prov.embed

    def spy(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        return orig(texts)

    prov.embed = spy  # type: ignore[method-assign]
    items = [(f"k{i}", f"text number {i}") for i in range(5)]
    with Session(eng) as db:
        out = embed_cached(db, prov, items, batch=2)
        assert set(out) == {f"k{i}" for i in range(5)} and calls == [2, 2, 1]
        calls.clear()
        out2 = embed_cached(db, prov, [*items, ("k9", "new")], batch=2)
        assert calls == [1] and len(out2) == 6
        assert np.allclose(out2["k3"], out["k3"])
        assert out2["k0"].dtype == np.float32


def handler_factory(
    seen: list[httpx.Request], responder: Any
) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return responder(req)  # type: ignore[no-any-return]

    return httpx.MockTransport(handler)


def test_ollama_embed_normalizes() -> None:
    seen: list[httpx.Request] = []
    t = handler_factory(
        seen, lambda r: httpx.Response(200, json={"embeddings": [[3.0, 4.0], [0.0, 2.0]]})
    )
    p = OllamaProvider(Settings(), transport=t)
    out = p.embed(["a", "b"])
    assert out == [[0.6, 0.8], [0.0, 1.0]]
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/api/embed"
    assert body == {"model": "nomic-embed-text", "input": ["a", "b"]}
    assert p.embed_model_id() == "ollama:nomic-embed-text"


def test_ollama_caption(tmp_path: Path) -> None:
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNGfake")
    seen: list[httpx.Request] = []
    t = handler_factory(seen, lambda r: httpx.Response(200, json={"response": " a red cat "}))
    p = OllamaProvider(Settings(), transport=t)
    assert p.caption(img) == "a red cat"
    body = json.loads(seen[0].content)
    assert body["model"] == "moondream" and body["stream"] is False and len(body["images"]) == 1


def test_ollama_chat_json() -> None:
    seen: list[httpx.Request] = []
    t = handler_factory(
        seen,
        lambda r: httpx.Response(200, json={"message": {"content": '{"choice": "A"}'}}),
    )
    p = OllamaProvider(Settings(), transport=t)
    schema = {"type": "object"}
    assert p.chat_json("pick", schema) == {"choice": "A"}
    body = json.loads(seen[0].content)
    assert body["format"] == schema and body["stream"] is False and body["model"] == "qwen2.5:7b-instruct"


def test_ollama_bad_json_content() -> None:
    t = handler_factory([], lambda r: httpx.Response(200, json={"message": {"content": "nope"}}))
    with pytest.raises(ModelUnavailable):
        OllamaProvider(Settings(), transport=t).chat_json("x", {})


def test_ollama_model_missing() -> None:
    t = handler_factory([], lambda r: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(ModelUnavailable, match="ollama pull nomic-embed-text"):
        OllamaProvider(Settings(), transport=t).embed(["x"])


def test_ollama_connection_error() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(ModelUnavailable, match="ollama serve"):
        OllamaProvider(Settings(), transport=httpx.MockTransport(boom)).embed(["x"])
