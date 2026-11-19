"""M4 acceptance: nested tree (depth 4) -> correct-path rate >= 75% and the rest in
the correct ancestor's inbox; batch cards; LLM rerank semantics (offline, scripted LLM)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from swipesort.config import Settings
from swipesort.main import create_app

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
from run_eval import run_eval


def test_m4_nested_descent() -> None:
    r = run_eval(n=300, seed=0, nested=True, provider="fake", decide=False)
    assert r.path_correct >= 0.75, r.model_dump(exclude={"misses"})
    assert r.dest_wrong <= 0.10, r.model_dump(exclude={"misses"})


def _tree(tmp: Path, n_inv: int = 5) -> tuple[Path, Path]:
    inp, out = tmp / "in", tmp / "out"
    for leaf, words in (("Finance/Invoices", "invoice amount due net 30 payment terms"),
                        ("School/Lectures", "lecture notes week syllabus exam homework")):
        (out / leaf).mkdir(parents=True)
        for i in range(3):
            (out / leaf / f"ex{i}.txt").write_text(f"{words} example {i}")
    inp.mkdir()
    for i in range(n_inv):
        (inp / f"invoice_{i}.txt").write_text(
            f"invoice amount due net 30 payment terms acme corp number {i}")
    return inp, out


def _client(tmp: Path, llm: Any = None, **batch: int) -> tuple[TestClient, Any]:
    s = Settings()
    s.models.provider = "fake"
    for k, v in batch.items():
        setattr(s.batch, k, v)
    app = create_app(db_path=tmp / "t.db", settings=s, llm=llm, static_dir=None)
    return TestClient(app), app


def _start(client: TestClient, app: Any, inp: Path, out: Path) -> int:
    sid = int(client.post("/api/sessions", json={"input_root": str(inp),
                                                  "output_root": str(out)}).json()["id"])
    assert app.state.service.pipelines[sid].wait(60)
    return sid


def test_batch_confirm_all_is_one_undo_step(tmp_path: Path) -> None:
    inp, out = _tree(tmp_path)
    client, app = _client(tmp_path, threshold=0)  # threshold 0: batch whatever is confirm-tier
    sid = _start(client, app, inp, out)
    cards = client.get(f"/api/sessions/{sid}/cards").json()
    batch = next((c for c in cards if c["kind"] == "batch"), None)
    assert batch is not None, [c["kind"] for c in cards]
    members = [m["id"] for m in batch["batch"]["members"]]
    assert len(members) >= 3
    r = client.post(f"/api/cards/{batch['id']}/decide",
                    json={"action": "confirm_all", "member_ids": members})
    assert r.status_code == 200, r.text
    target = out / batch["batch"]["target_path"]
    assert all((target / m["name"]).exists() for m in batch["batch"]["members"])
    client.post(f"/api/sessions/{sid}/undo", json={"steps": 1})
    assert all((inp / m["name"]).exists() for m in batch["batch"]["members"])
    assert not any((target / m["name"]).exists() for m in batch["batch"]["members"])


def test_batch_review_returns_individual_cards(tmp_path: Path) -> None:
    inp, out = _tree(tmp_path)
    client, app = _client(tmp_path, threshold=0)
    sid = _start(client, app, inp, out)
    batch = next(c for c in client.get(f"/api/sessions/{sid}/cards").json()
                 if c["kind"] == "batch")
    members = [m["id"] for m in batch["batch"]["members"]]
    client.post(f"/api/cards/{batch['id']}/decide",
                json={"action": "review", "member_ids": members})
    ids = {c["id"] for c in client.get(f"/api/sessions/{sid}/cards").json()}
    assert batch["id"] not in ids and {f"m{i}" for i in members} <= ids
    client.post(f"/api/sessions/{sid}/undo", json={"steps": 1})
    ids = {c["id"] for c in client.get(f"/api/sessions/{sid}/cards").json()}
    assert batch["id"] in ids


class ScriptedLLM:
    """Always answers with a fixed folder (or the first candidate when told to agree)."""

    def __init__(self, choice: str | None) -> None:
        self.choice, self.calls = choice, 0

    def model_id(self) -> str:
        return "scripted"

    def chat_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        listing = [ln.split(". ", 1)[1] for ln in prompt.splitlines()
                   if ln[:1].isdigit() and ". " in ln]
        return {"choice": self.choice or listing[0], "alternates": [],
                "new_folder_suggestion": None, "reason": "Scripted reason from the file"}


def test_llm_disagreement_caps_confirm(tmp_path: Path) -> None:
    inp, out = _tree(tmp_path, n_inv=2)
    llm = ScriptedLLM("School/Lectures")  # disagrees with the obvious Finance/Invoices
    client, app = _client(tmp_path, llm=llm, min_size=10**9, inbox_min_size=10**9)
    sid = _start(client, app, inp, out)
    assert app.state.service.pipelines[sid].rerank_idle.wait(30)
    cards = client.get(f"/api/sessions/{sid}/cards").json()
    assert llm.calls >= 1
    for c in cards:
        assert c["kind"] != "confirm"  # capped below 0.9 on disagreement
        assert c["confidence"] < 0.9
        assert c["candidates"][0]["path"] == "School/Lectures"  # LLM's pick leads Choices
        assert c["reason"] == "Scripted reason from the file"
