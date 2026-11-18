"""M0 acceptance (SPEC §14): sort 50 fixture files by hand through the API, then undo all
50 decisions and verify the original input + output trees are restored exactly (checksums)."""

from __future__ import annotations

from pathlib import Path

import blake3
import pytest
from fastapi.testclient import TestClient

from swipesort.config import Settings
from swipesort.main import create_app
from tests.fixtures.generate_corpus import generate


def snapshot(root: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if rel.split("/")[0] == ".swipesort":
            continue
        snap[rel] = "<dir>" if p.is_dir() else blake3.blake3(p.read_bytes()).hexdigest()
    return snap


@pytest.fixture()
def corpus(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    m = generate(tmp_path / "c", n=50, seed=7, nested=True)
    return tmp_path / "c" / "input", tmp_path / "c" / "output", {
        e.rel_path: e.category for e in m.entries
    }


def test_sort_50_by_hand_then_undo_all(
    tmp_path: Path, corpus: tuple[Path, Path, dict[str, str]]
) -> None:
    inp, out, truth = corpus
    before_in, before_out = snapshot(inp), snapshot(out)
    settings = Settings()
    settings.models.provider = "fake"
    settings.batch.min_size = settings.batch.inbox_min_size = 10**9  # per-file test
    app = create_app(db_path=tmp_path / "s.db", settings=settings, static_dir=None)
    client = TestClient(app)

    r = client.post("/api/sessions", json={"input_root": str(inp), "output_root": str(out)})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert r.json()["progress"]["scanned"] == 50
    assert app.state.service.pipelines[sid].wait(120)

    def folder_id(rel: str) -> int:
        folders = {f["path"]: f["id"] for f in client.get(f"/api/sessions/{sid}/folders").json()}
        if rel in folders:
            return folders[rel]
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        pid = folder_id(parent) if parent else None
        r = client.post("/api/folders",
                        json={"session_id": sid, "parent_id": pid, "name": rel.split("/")[-1]})
        assert r.status_code == 200, r.text
        return int(r.json()["id"])

    decisions = 0
    deferred_once = False
    while True:
        cards = client.get(f"/api/sessions/{sid}/cards", params={"limit": 5}).json()
        if not cards:
            break
        card = cards[0]
        rel = card["file"]["rel_path"]
        assert set(card["edges"]) >= {"left", "up", "down"}
        if not deferred_once:
            body: dict[str, object] = {"action": "defer"}
            deferred_once = True
        elif decisions % 9 == 4:
            body = {"action": "trash"}
        else:
            body = {"action": "pick", "target_folder_id": folder_id(truth[rel])}
        r = client.post(f"/api/cards/{card['id']}/decide", json=body)
        assert r.status_code == 200, r.text
        decisions += 1

    sess = client.get(f"/api/sessions/{sid}").json()
    assert sess["progress"]["decided"] == 50 and sess["progress"]["remaining"] == 0
    assert snapshot(inp) == {k: v for k, v in snapshot(inp).items() if v == "<dir>"}  # files gone
    tree = client.get(f"/api/sessions/{sid}/tree", params={"side": "output"}).json()
    assert tree["children"]

    # Undo everything (moves, trash, mkdirs, the defer) in one call.
    r = client.post(f"/api/sessions/{sid}/undo", json={"steps": 10_000})
    assert r.status_code == 200, r.text
    assert r.json()["progress"]["remaining"] == 50

    assert snapshot(inp) == before_in
    assert snapshot(out) == before_out
    assert len(client.get(f"/api/sessions/{sid}/cards", params={"limit": 100}).json()) == 50
