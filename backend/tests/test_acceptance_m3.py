"""M3 acceptance (SPEC §14): ≥ 15 READY cards maintained while swiping on 1k files; creating a
folder updates pending cards (pushed over the WebSocket)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from swipesort.config import Settings
from swipesort.main import create_app
from swipesort.ml.providers.fake import FakeProvider

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from generate_corpus import generate

LOOKAHEAD = 15


class SlowProvider(FakeProvider):
    """Fake embeddings at a CPU-only-laptop pace (20 ms per text)."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        time.sleep(0.02 * len(texts))
        return super().embed(texts)


def _settings() -> Settings:
    s = Settings()
    s.models.provider = "fake"
    return s


def test_lookahead_buffer_while_swiping_1k(tmp_path: Path) -> None:
    generate(tmp_path / "c", n=1000, seed=11)
    s = _settings()
    app = create_app(db_path=tmp_path / "t.db", settings=s, provider=SlowProvider(s),
                     static_dir=None)
    client = TestClient(app)
    t0 = time.perf_counter()
    sid = client.post("/api/sessions", json={
        "input_root": str(tmp_path / "c" / "input"),
        "output_root": str(tmp_path / "c" / "output")}).json()["id"]

    def ready() -> int:
        return int(client.get(f"/api/sessions/{sid}").json()["progress"]["ready"])

    deadline = time.perf_counter() + 180
    while ready() < LOOKAHEAD:  # warm-up: CPU pass over all files + first small batch
        assert time.perf_counter() < deadline, "no lookahead buffer within 180 s"
        time.sleep(0.1)
    first_buffer_s = time.perf_counter() - t0
    pipe = app.state.service.pipelines[sid]
    assert not pipe.done.is_set(), "pipeline should still be streaming, not all-at-once"

    folders = client.get(f"/api/sessions/{sid}/folders").json()
    target = next(f["id"] for f in folders if f["path"])
    low = 10**9
    for _ in range(120):  # 4 cards/sec: 4x faster than the spec's 1 card/sec
        cards = client.get(f"/api/sessions/{sid}/cards", params={"limit": 20}).json()
        low = min(low, len(cards))
        r = client.post(f"/api/cards/{cards[0]['id']}/decide",
                        json={"action": "pick", "target_folder_id": target})
        assert r.status_code == 200, r.text
        time.sleep(0.25)
    assert low >= LOOKAHEAD, f"buffer dipped to {low} (first buffer after {first_buffer_s:.1f}s)"
    print(f"first {LOOKAHEAD} ready after {first_buffer_s:.1f}s; min buffer {low}")


def test_new_folder_updates_pending_cards_over_ws(tmp_path: Path) -> None:
    inp, out = tmp_path / "in", tmp_path / "out"
    (out / "Finance" / "Invoices").mkdir(parents=True)
    (out / "School" / "Lectures").mkdir(parents=True)
    (out / "Finance" / "Invoices" / "inv.txt").write_text("invoice amount due net 30 acme")
    (out / "School" / "Lectures" / "l1.txt").write_text("lecture notes week one syllabus")
    inp.mkdir()
    for i in range(4):
        (inp / f"doc{i}.txt").write_text(
            f"mortgage statement escrow balance principal loan servicer month {i}")
    s = _settings()
    app = create_app(db_path=tmp_path / "t.db", settings=s, static_dir=None)
    client = TestClient(app)
    sid = client.post("/api/sessions", json={"input_root": str(inp),
                                             "output_root": str(out)}).json()["id"]
    assert app.state.service.pipelines[sid].wait(60)
    before = {c["id"]: c["candidates"][0]["path"]
              for c in client.get(f"/api/sessions/{sid}/cards").json()}
    assert all(p != "Finance/Mortgage" for p in before.values())

    with client.websocket_connect(f"/api/sessions/{sid}/stream") as ws:
        assert ws.receive_json()["type"] == "hello"
        fin = next(f["id"] for f in client.get(f"/api/sessions/{sid}/folders").json()
                   if f["path"] == "Finance")
        r = client.post("/api/folders", json={"session_id": sid, "parent_id": fin,
                                              "name": "Mortgage"})
        assert r.status_code == 200, r.text
        updated: set[str] = set()
        deadline = time.perf_counter() + 10
        while len(updated) < len(before) and time.perf_counter() < deadline:
            ev = ws.receive_json()
            if ev["type"] == "card_updated":
                assert ev["card"]["candidates"][0]["path"] == "Finance/Mortgage"
                updated.add(ev["card"]["id"])
        assert updated == set(before), (updated, before)

    after = client.get(f"/api/sessions/{sid}/cards").json()
    assert all(c["candidates"][0]["path"] == "Finance/Mortgage" for c in after)
