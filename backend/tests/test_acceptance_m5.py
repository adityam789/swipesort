"""M5 acceptance (SPEC §14): empty output + 300 mixed files -> a sensible proposed structure
that rediscovers the seeded hidden categories; low-confidence routing (§6.1) groups files of a
missing category into one new-folder card. Offline (fake embeddings, fallback names)."""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session as DB
from sqlmodel import col, select

from swipesort.config import Settings
from swipesort.db.models import Decision, File, Op, OpType, Proposal, Suggestion
from swipesort.main import create_app

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from generate_corpus import generate
from run_cold_eval import run_cold_eval


def test_m5_cold_start_rediscovers_hidden_categories(tmp_path: Path) -> None:
    r = run_cold_eval(n=300, seed=0, provider="fake", workdir=tmp_path)
    summary = r.model_dump(exclude={"tree"})
    assert 5 <= r.n_top <= 12, summary
    assert r.depth <= 2
    assert r.rediscovery >= 0.75, summary
    assert r.purity >= 0.80, summary
    assert not r.generic_names, summary
    # accepting the structure seeds the new folders: files score into "their" cluster
    assert r.after_category_ok >= 0.9, summary
    assert r.after_tiers.get("confirm", 0) >= 0.8 * r.n, summary


def _settings(**proposals: Any) -> Settings:
    s = Settings()
    s.models.provider = "fake"
    for k, v in proposals.items():
        setattr(s.proposals, k, v)
    return s


def _start(client: TestClient, app: Any, inp: Path, out: Path) -> int:
    sid = int(client.post("/api/sessions", json={"input_root": str(inp),
                                                  "output_root": str(out)}).json()["id"])
    pipe = app.state.service.pipelines[sid]
    assert pipe.wait(120) and pipe.proposals_idle.wait(30)
    return sid


def _tree(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")
            if ".swipesort" not in p.parts}


def test_taxonomy_card_accept_subset_rename_and_undo(tmp_path: Path) -> None:
    generate(tmp_path / "c", n=120, seed=4, cold=True)
    inp, out = tmp_path / "c" / "input", tmp_path / "c" / "output"
    app = create_app(db_path=tmp_path / "t.db", settings=_settings(), static_dir=None)
    client = TestClient(app)
    sid = _start(client, app, inp, out)
    cards = client.get(f"/api/sessions/{sid}/cards", params={"limit": 200}).json()
    assert cards[0]["kind"] == "taxonomy", [c["kind"] for c in cards]
    # while the structure is pending, only it (and duplicate cards) are shown
    assert {c["kind"] for c in cards} <= {"taxonomy", "duplicate"}
    tax = cards[0]["taxonomy"]
    assert 2 <= len(tax["nodes"]) <= 12
    chosen = tax["nodes"][:2]
    accepted = [n["id"] for n in chosen] + [k["id"] for n in chosen for k in n["children"]]
    r = client.post(f"/api/cards/{cards[0]['id']}/decide", json={
        "action": "accept", "accepted_nodes": accepted, "renames": {chosen[0]["id"]: "Money"}})
    assert r.status_code == 200, r.text
    want = {"Money", chosen[1]["name"]}
    want |= {f"Money/{k['name']}" for k in chosen[0]["children"]}
    want |= {f"{chosen[1]['name']}/{k['name']}" for k in chosen[1]["children"]}
    assert _tree(out) == want  # unselected nodes were not created; files not moved
    pipe = app.state.service.pipelines[sid]
    pipe.rescore_stale()
    cards = client.get(f"/api/sessions/{sid}/cards", params={"limit": 200}).json()
    kinds = {c["kind"] for c in cards}
    assert "taxonomy" not in kinds and kinds & {"batch", "confirm", "choices"}
    dests = {c["destination"] for c in cards if c["kind"] in ("confirm", "batch")}
    assert dests & want, dests

    client.post(f"/api/sessions/{sid}/undo", json={"steps": 1})  # one step: all folders
    assert _tree(out) == set()
    cards = client.get(f"/api/sessions/{sid}/cards").json()
    assert cards[0]["kind"] == "taxonomy"

    # dismiss -> the files come back as ordinary cards
    r = client.post(f"/api/proposals/{tax['proposal_id']}/decide", json={"action": "dismiss"})
    assert r.status_code == 200, r.text
    kinds = {c["kind"] for c in client.get(f"/api/sessions/{sid}/cards").json()}
    assert "taxonomy" not in kinds and kinds - {"duplicate"}


def _resume_ids(app: Any, sid: int, truth: dict[str, str]) -> set[int]:
    with DB(app.state.service.engine) as db:
        return {f.id for f in db.exec(select(File).where(File.session_id == sid)).all()
                if truth[f.rel_path] == "Work/Resumes" and f.id is not None}


def _latest(db: DB, fid: int) -> Suggestion:
    sg = db.exec(select(Suggestion).where(Suggestion.file_id == fid,
                                          Suggestion.kind == "classify")
                 .order_by(col(Suggestion.id).desc())).first()
    assert sg is not None
    return sg


def test_low_confidence_files_of_a_missing_category_become_a_folder(tmp_path: Path) -> None:
    m = generate(tmp_path / "c", n=300, seed=0)
    truth = {e.rel_path: e.category for e in m.entries}
    inp, out = tmp_path / "c" / "input", tmp_path / "c" / "output"
    shutil.rmtree(out / "Work" / "Resumes")  # the category the user doesn't have yet
    app = create_app(db_path=tmp_path / "t.db", settings=_settings(), static_dir=None)
    client = TestClient(app)
    sid = _start(client, app, inp, out)
    resumes = _resume_ids(app, sid, truth)
    cards = client.get(f"/api/sessions/{sid}/cards", params={"limit": 200}).json()
    props = [c for c in cards if c["kind"] == "folder_proposal"]
    groups = [{mm["id"] for mm in c["proposal"]["members"]} for c in props]
    hits = [(c, g) for c, g in zip(props, groups, strict=True)
            if len(g & resumes) / len(g) >= 0.7]
    assert hits, [(c["reason"], len(g & resumes)) for c, g in zip(props, groups, strict=True)]
    # rediscovered: the proposals cover most of the missing category, members not shown alone
    assert len(set().union(*(g for _, g in hits)) & resumes) >= 0.6 * len(resumes)
    shown = {c["file"]["id"] for c in cards if c["kind"] in ("confirm", "choices", "inbox")}
    assert not shown & set().union(*groups)
    card, members = max(hits, key=lambda h: len(h[1]))
    p = card["proposal"]
    assert p["name"].casefold() not in ("misc", "other", "files", "documents")
    assert card["reason"] == f"Create {p['path']}/ for these {len(members)} files?"
    assert set(card["edges"]) == {"right", "up", "left"}

    with DB(app.state.service.engine) as db:  # the other pending resumes, before
        others = {i: _latest(db, i) for i in resumes - members
                  if (f := db.get(File, i)) is not None and f.status == "ready"}
        before = {i: (sg.confidence, json.loads(sg.candidates_json)[0]["path"])
                  for i, sg in others.items()}
    assert before, "expected other resumes left to rescore"

    r = client.post(f"/api/proposals/{p['proposal_id']}/decide", json={"action": "accept"})
    assert r.status_code == 200, r.text
    dest = out / p["path"]
    assert dest.is_dir()
    names = {mm["name"] for mm in p["members"]}
    assert {x.name for x in dest.iterdir()} == names
    with DB(app.state.service.engine) as db:  # mkdir + every move journaled, one decision
        dec = db.get(Decision, r.json()["decision_id"])
        assert dec is not None and dec.action == "proposal_accept"
        ops = db.exec(select(Op).where(Op.decision_id == dec.id)).all()
        assert sum(o.type == OpType.mkdir for o in ops) >= 1
        assert sum(o.type == OpType.move for o in ops) == len(members)

    # §6.1 step 5: re-scored, the remaining resumes now land in the new folder, more confident
    deadline = time.monotonic() + 30
    improved: dict[int, tuple[float, str]] = {}
    while time.monotonic() < deadline:
        with DB(app.state.service.engine) as db:
            improved = {i: (sg.confidence, json.loads(sg.candidates_json)[0]["path"])
                        for i in before if (sg := _latest(db, i)) is not None}
        if all(v[1] == p["path"] and v[0] > before[i][0] for i, v in improved.items()):
            break
        time.sleep(0.2)
    ok = [i for i, v in improved.items() if v[1] == p["path"] and v[0] > before[i][0]]
    assert len(ok) >= 0.8 * len(before), (before, improved)
    with DB(app.state.service.engine) as db:  # superseded proposals are withdrawn
        for c, _g in hits:
            if c["id"] != card["id"]:
                pr = db.get(Proposal, c["proposal"]["proposal_id"])
                assert pr is not None and pr.status == "withdrawn"

    # undo: one step restores every member and removes the created folder
    client.post(f"/api/sessions/{sid}/undo", json={"steps": 1})
    assert not dest.exists()
    with DB(app.state.service.engine) as db:
        for i in members:
            f = db.get(File, i)
            assert f is not None and f.status == "ready" and Path(f.path).is_file()
            assert Path(f.path).is_relative_to(inp)
    ids = {c["id"] for c in client.get(f"/api/sessions/{sid}/cards",
                                       params={"limit": 200}).json()}
    assert card["id"] in ids


def _small(tmp: Path, n: int = 5) -> tuple[Path, Path]:
    inp, out = tmp / "in", tmp / "out"
    for leaf, words in (("Finance/Invoices", "invoice amount due net 30 payment terms"),
                        ("School/Lectures", "lecture notes week syllabus exam homework")):
        (out / leaf).mkdir(parents=True)
        for i in range(3):
            (out / leaf / f"ex{i}.txt").write_text(f"{words} example {i}")
    inp.mkdir()
    for i in range(n):
        (inp / f"loan_{i}.txt").write_text(
            f"mortgage statement escrow balance principal loan servicer month {i}")
    return inp, out


def test_proposal_rename_inbox_dismiss(tmp_path: Path) -> None:
    inp, out = _small(tmp_path)
    app = create_app(db_path=tmp_path / "t.db", settings=_settings(), static_dir=None)
    client = TestClient(app)
    sid = _start(client, app, inp, out)
    cards = client.get(f"/api/sessions/{sid}/cards").json()
    assert [c["kind"] for c in cards] == ["folder_proposal"], cards
    card = cards[0]
    pid = card["proposal"]["proposal_id"]
    assert client.post(f"/api/proposals/{pid}/decide",
                       json={"action": "rename", "name": "Misc"}).status_code == 400
    r = client.post(f"/api/proposals/{pid}/decide", json={"action": "rename", "name": "Home Loan"})
    assert r.status_code == 200, r.text
    card = client.get(f"/api/sessions/{sid}/cards").json()[0]
    assert card["proposal"]["path"] == "Home Loan" and card["proposal"]["named_by"] == "user"

    # Up: send the group to the stop node's _Inbox instead (one undo step)
    r = client.post(f"/api/cards/{card['id']}/decide", json={"action": "inbox"})
    assert r.status_code == 200, r.text
    assert len(list((out / "_Inbox").iterdir())) == 5
    assert client.get(f"/api/sessions/{sid}/cards").json() == []
    client.post(f"/api/sessions/{sid}/undo", json={"steps": 1})
    assert not (out / "_Inbox").exists() and len(list(inp.iterdir())) == 5
    assert client.get(f"/api/sessions/{sid}/cards").json()[0]["id"] == card["id"]

    # Left: dismiss -> members come back as individual cards; undo brings the card back
    client.post(f"/api/cards/{card['id']}/decide", json={"action": "dismiss"})
    kinds = [c["kind"] for c in client.get(f"/api/sessions/{sid}/cards").json()]
    assert "folder_proposal" not in kinds and len(kinds) >= 1
    client.post(f"/api/sessions/{sid}/undo", json={"steps": 1})
    assert client.get(f"/api/sessions/{sid}/cards").json()[0]["id"] == card["id"]

    # accept with a name (Right after an inline rename)
    r = client.post(f"/api/cards/{card['id']}/decide", json={"action": "accept",
                                                              "name": "Mortgage"})
    assert r.status_code == 200, r.text
    assert len(list((out / "Mortgage").iterdir())) == 5
    assert client.post(f"/api/cards/{card['id']}/decide",
                       json={"action": "accept"}).status_code == 400  # already decided


def test_members_decided_one_by_one_drop_out(tmp_path: Path) -> None:
    inp, out = _small(tmp_path, n=4)
    app = create_app(db_path=tmp_path / "t.db", settings=_settings(), static_dir=None)
    client = TestClient(app)
    sid = _start(client, app, inp, out)
    card = client.get(f"/api/sessions/{sid}/cards").json()[0]
    assert card["kind"] == "folder_proposal" and len(card["proposal"]["members"]) == 4
    svc = app.state.service
    folders = {f["path"]: f["id"] for f in client.get(f"/api/sessions/{sid}/folders").json()}
    for m in card["proposal"]["members"][:2]:  # e.g. decided via the folder picker elsewhere
        r = client.post(f"/api/cards/m{m['id']}/decide",
                        json={"action": "pick", "target_folder_id": folders["Finance"]})
        assert r.status_code == 200, r.text
    assert svc.proposal_card(card["proposal"]["proposal_id"]) is None  # 2 < min_cluster
    cards = client.get(f"/api/sessions/{sid}/cards").json()
    assert all(c["kind"] != "folder_proposal" for c in cards) and len(cards) == 2


def test_inbox_batch_suggest_folder_anyway(tmp_path: Path) -> None:
    inp, out = _small(tmp_path, n=0)
    for i, words in enumerate(("garden tomato seeds compost", "violin rosin bow strings",
                               "kayak paddle river trip")):
        (inp / f"note{i}.txt").write_text(words)
    s = _settings(cohesion=0.99, singleton_max=0, by_provider={})  # nothing qualifies
    app = create_app(db_path=tmp_path / "t.db", settings=s, static_dir=None)
    client = TestClient(app)
    sid = _start(client, app, inp, out)
    cards = client.get(f"/api/sessions/{sid}/cards").json()
    batch = next(c for c in cards if c["kind"] == "batch")
    assert batch["edges"]["up"]["action"] == "suggest_folder"
    members = [m["id"] for m in batch["batch"]["members"]]
    r = client.post(f"/api/cards/{batch['id']}/decide",
                    json={"action": "suggest_folder", "member_ids": members})
    assert r.status_code == 200, r.text
    cards = client.get(f"/api/sessions/{sid}/cards").json()
    assert cards[0]["kind"] == "folder_proposal"
    assert {m["id"] for m in cards[0]["proposal"]["members"]} == set(members)
    assert all(c["kind"] == "folder_proposal" for c in cards)


def test_cold_corpus_has_the_same_input_and_an_empty_output(tmp_path: Path) -> None:
    warm = generate(tmp_path / "warm", n=40, seed=2)
    cold = generate(tmp_path / "cold", n=40, seed=2, cold=True)
    assert [e.rel_path for e in warm.entries] == [e.rel_path for e in cold.entries]
    for e in warm.entries:
        a, b = tmp_path / "warm" / "input" / e.rel_path, tmp_path / "cold" / "input" / e.rel_path
        assert a.is_dir() == b.is_dir()
        if a.is_file():
            assert a.read_bytes() == b.read_bytes()
    assert list((tmp_path / "cold" / "output").iterdir()) == [] and not cold.examples
