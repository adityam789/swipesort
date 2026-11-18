"""M2 acceptance through the API (SPEC §14): duplicate cards come first, exact dupes are all
found and are true byte-identical copies, and keep_this / keep_both are undoable.
Recall/precision on the full corpus is measured by eval/run_dedup_eval.py (also asserted)."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from swipesort.config import Settings
from swipesort.main import create_app

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from generate_corpus import generate
from run_dedup_eval import eval_dedup


def test_m2_dedup_eval_thresholds() -> None:
    for hard in (False, True):
        r = eval_dedup(n=300, seed=0, hard=hard)
        assert r.exact_recall == 1.0, r
        assert r.near_recall >= 0.90, r
        assert r.false_exact == 0, r


def _setup(tmp_path: Path) -> tuple[TestClient, int, Path, dict[str, str]]:
    m = generate(tmp_path / "c", n=120, seed=5)
    s = Settings()
    s.models.provider = "fake"
    app = create_app(db_path=tmp_path / "t.db", settings=s, static_dir=None)
    client = TestClient(app)
    sid = int(client.post("/api/sessions", json={
        "input_root": str(tmp_path / "c" / "input"),
        "output_root": str(tmp_path / "c" / "output")}).json()["id"])
    assert app.state.service.pipelines[sid].wait(120)
    dup_of = {e.rel_path: e.dup_of for e in m.entries if e.dup_of}
    return client, sid, tmp_path / "c" / "input", dup_of  # type: ignore[return-value]


def test_duplicate_cards_first_and_exact_pairs_found(tmp_path: Path) -> None:
    client, sid, inp, dup_of = _setup(tmp_path)
    assert dup_of, "corpus should seed exact duplicates"
    cards = client.get(f"/api/sessions/{sid}/cards", params={"limit": 200}).json()
    kinds = [c["kind"] for c in cards]
    n_dup = kinds.count("duplicate")
    assert n_dup and all(k == "duplicate" for k in kinds[:n_dup])  # duplicates first
    pairs = {frozenset((c["file"]["rel_path"], c["duplicate"]["other_rel_path"]))
             for c in cards if c["kind"] == "duplicate" and c["duplicate"]["relation"] == "exact"}
    for a, b in dup_of.items():
        assert frozenset((a, b)) in pairs or any(a in p for p in pairs), (a, b)
    for c in cards:
        if c["kind"] == "duplicate" and c["duplicate"]["relation"] == "exact":
            assert c["duplicate"]["other_side"] == "input"
            assert (inp / c["file"]["rel_path"]).read_bytes() == (
                inp / c["duplicate"]["other_rel_path"]).read_bytes()


def test_keep_this_trashes_other_and_undo_restores(tmp_path: Path) -> None:
    client, sid, inp, _ = _setup(tmp_path)
    card = next(c for c in client.get(f"/api/sessions/{sid}/cards").json()
                if c["kind"] == "duplicate")
    other = inp / card["duplicate"]["other_rel_path"]
    assert other.exists()
    r = client.post(f"/api/cards/{card['id']}/decide", json={"action": "keep_this"})
    assert r.status_code == 200, r.text
    assert not other.exists()
    after = client.get(f"/api/sessions/{sid}/cards", params={"limit": 200}).json()
    ids = {c["id"] for c in after}
    assert card["id"] not in ids and f"m{card['file']['id']}" in ids  # continues to classify
    client.post(f"/api/sessions/{sid}/undo", json={"steps": 1})
    assert other.exists()
    ids = {c["id"] for c in client.get(f"/api/sessions/{sid}/cards",
                                        params={"limit": 200}).json()}
    assert card["id"] in ids


def test_keep_both_then_undo(tmp_path: Path) -> None:
    client, sid, _inp, _ = _setup(tmp_path)
    card = next(c for c in client.get(f"/api/sessions/{sid}/cards").json()
                if c["kind"] == "duplicate")
    client.post(f"/api/cards/{card['id']}/decide", json={"action": "keep_both"})
    ids = {c["id"] for c in client.get(f"/api/sessions/{sid}/cards",
                                        params={"limit": 200}).json()}
    assert card["id"] not in ids and f"m{card['file']['id']}" in ids
    client.post(f"/api/sessions/{sid}/undo", json={"steps": 1})
    ids = {c["id"] for c in client.get(f"/api/sessions/{sid}/cards",
                                        params={"limit": 200}).json()}
    assert card["id"] in ids
