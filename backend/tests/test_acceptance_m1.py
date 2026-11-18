"""M1 acceptance (SPEC §14): top-1 ≥ 80%, top-3 ≥ 95%, card → next card < 100 ms.

Runs offline with the fake provider (CLAUDE.md); `eval/run_eval.py --provider ollama` is the
real-model run.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from swipesort.config import Settings
from swipesort.main import create_app
from swipesort.ml.gateway import ModelUnavailable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
from run_eval import run_eval


def test_m1_accuracy_and_latency(tmp_path: Path) -> None:
    r = run_eval(n=300, seed=0, provider="fake", workdir=tmp_path, hard=True)
    assert r.n == 300
    assert r.top1 >= 0.80, r.misses[:10]
    assert r.top3 >= 0.95, r.misses[:10]
    assert r.next_cards_ms_p95 < 100


def _client(tmp_path: Path, provider: Any = None) -> tuple[TestClient, Any]:
    s = Settings()
    s.models.provider = "fake"
    s.batch.min_size = s.batch.inbox_min_size = 10**9  # per-file test
    app = create_app(db_path=tmp_path / "t.db", settings=s, provider=provider, static_dir=None)
    return TestClient(app), app


def _session(tmp_path: Path, client: TestClient, app: Any) -> int:
    inp, out = tmp_path / "in", tmp_path / "out"
    (out / "Finance" / "Invoices").mkdir(parents=True)
    (out / "School" / "Lectures").mkdir(parents=True)
    (out / "Finance" / "Invoices" / "a.txt").write_text("invoice amount due acme corp net 30")
    (out / "Finance" / "Invoices" / "b.txt").write_text("invoice total due initech payment terms")
    (out / "School" / "Lectures" / "l1.txt").write_text("lecture notes week one syllabus exam")
    (out / "School" / "Lectures" / "l2.txt").write_text("lecture slides week two homework quiz")
    inp.mkdir()
    (inp / "mystery.txt").write_text("zebra quantum lullaby")
    (inp / "inv.txt").write_text("invoice amount due initech")
    sid = int(client.post("/api/sessions", json={"input_root": str(inp),
                                                  "output_root": str(out)}).json()["id"])
    assert app.state.service.pipelines[sid].wait(60)
    return sid


def test_ai_cards_have_data_driven_edges(tmp_path: Path) -> None:
    client, app = _client(tmp_path)
    sid = _session(tmp_path, client, app)
    cards = client.get(f"/api/sessions/{sid}/cards").json()
    assert {c["kind"] for c in cards} <= {"confirm", "choices", "inbox"}
    for c in cards:
        assert c["edges"]["down"]["action"] == "trash"
        assert c["confidence"] is not None and c["candidates"]
        if c["kind"] == "confirm":
            assert c["edges"]["left"]["action"] == "flip" and c["flip_edges"]
        if c["kind"] == "choices":
            assert set(c["keys"]) <= {"1", "2", "3"}


def test_inbox_then_undo_removes_inbox(tmp_path: Path) -> None:
    client, app = _client(tmp_path)
    sid = _session(tmp_path, client, app)
    cards = client.get(f"/api/sessions/{sid}/cards").json()
    card = next(c for c in cards if c["file"]["name"] == "mystery.txt")
    assert card["kind"] == "inbox"
    r = client.post(f"/api/cards/{card['id']}/decide", json={"action": "inbox"})
    assert r.status_code == 200, r.text
    # SPEC §4: the inbox lives at the level where descent stopped (card.destination)
    inbox = tmp_path / "out" / card["destination"]
    assert inbox.name == "_Inbox" and (inbox / card["file"]["name"]).exists()
    client.post(f"/api/sessions/{sid}/undo", json={"steps": 1})
    assert not inbox.exists()
    assert (tmp_path / "in" / card["file"]["name"]).exists()
    ids = [c["id"] for c in client.get(f"/api/sessions/{sid}/cards").json()]
    assert card["id"] in ids


class DownProvider:
    name = "down"

    def embed_model_id(self) -> str:
        return "down"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ModelUnavailable("ollama not running")

    def caption(self, image: Path) -> str:
        raise ModelUnavailable("ollama not running")

    def chat_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise ModelUnavailable("ollama not running")


def test_model_down_degrades_to_manual_cards(tmp_path: Path) -> None:
    client, app = _client(tmp_path, provider=DownProvider())
    sid = _session(tmp_path, client, app)
    cards = client.get(f"/api/sessions/{sid}/cards").json()
    assert len(cards) == 2 and all(c["kind"] == "manual" for c in cards)
    assert app.state.service.pipelines[sid].degraded
