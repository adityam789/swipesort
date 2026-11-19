"""M7 acceptance (SPEC §14, §18): drop 20 files into a watched Downloads folder: high-confidence
ones are auto-sorted within 10 s of finishing download; the rest appear as cards; every
auto-move is undoable.

Real watchdog observer, fake embedding provider, fixture corpus as the output tree. Downloads
are simulated the way browsers do them: `name.crdownload` written in chunks then renamed,
files written in place, files held locked for a while (a real flock on POSIX / an open handle
on Windows, which `platform.is_locked` detects), a dropped code-project bundle, exact
duplicates of output-tree files, unplaceable files, and abandoned partial downloads.

Thresholds: the production default stays auto_move_threshold = 0.97 and
min_decisions_for_auto = 50 (SPEC §18). Here min_decisions_for_auto = 0 (fresh database) and
the threshold is 0.95: the fake hashing embeddings put clean corpus files in the Confirm tier at
~0.95-1.0 (see the M1 eval), so 0.95 makes "high confidence" mean the same thing as 0.97 does
for a calibrated real model.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DB
from sqlmodel import col, select

from swipesort.config import Settings, load_settings
from swipesort.db.models import Decision, File, FileStatus, Op, OpStatus
from swipesort.exec.executor import Executor
from swipesort.main import create_app
from swipesort.platform import get_platform

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from generate_corpus import generate

LATENCY_BOUND_S = 10.0
SETTLE_S = 0.5


def _platform_class() -> type:
    return type(get_platform())


class RecordingPlatform(_platform_class()):  # type: ignore[misc]
    """The real OS platform (real is_locked), with notifications recorded instead of shown."""

    def __init__(self) -> None:
        super().__init__()
        self.notes: list[tuple[str, str, str | None]] = []
        self.lock_checks = 0

    def notify(self, title: str, body: str, undo_url: str | None = None) -> None:
        self.notes.append((title, body, undo_url))

    def is_locked(self, path: Path) -> bool:
        self.lock_checks += 1
        return bool(super().is_locked(path))


@contextlib.contextmanager
def held_lock(path: Path) -> Iterator[None]:
    """Hold `path` the way a downloading browser does: exclusively locked."""
    fh = open(path, "rb")  # noqa: SIM115 - Windows: an open handle is a sharing lock
    try:
        try:
            import fcntl  # POSIX: an exclusive flock (what platform.is_locked probes)

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        yield
    finally:
        fh.close()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _write_chunks(path: Path, data: bytes, parts: int = 3) -> None:
    step = max(1, len(data) // parts)
    with open(path, "wb") as fh:
        for i in range(0, len(data), step):
            fh.write(data[i:i + step])
            fh.flush()
            time.sleep(0.05)


def _settings() -> Settings:
    s = Settings()
    s.models.provider = "fake"
    s.batch.min_size = s.batch.inbox_min_size = 10**9  # per-file cards
    s.proposals.enabled = False  # pending files stay individual cards
    s.watch.settle_seconds = SETTLE_S
    s.watch.settle_poll_seconds = 0.1
    s.watch.rescan_seconds = 1.0
    s.watch.min_decisions_for_auto = 0
    s.watch.auto_move_threshold = 0.95
    s.watch.notify = True
    return s


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setenv("SWIPESORT_HOME", str(tmp_path / "home"))
    manifest = generate(tmp_path / "corpus", n=60, seed=7)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "old-report.pdf").write_bytes(b"%PDF-1.4 already here before watching")
    plat = RecordingPlatform()
    app = create_app(db_path=tmp_path / "t.db", settings=_settings(), platform=plat,
                     static_dir=None)
    client = TestClient(app)
    yield {"client": client, "app": app, "manifest": manifest, "downloads": downloads,
           "out": tmp_path / "corpus" / "output", "inp": tmp_path / "corpus" / "input",
           "platform": plat}
    app.state.watch.stop()
    for pipe in app.state.service.pipelines.values():
        pipe.stop()


def test_m7_watch_downloads(env: dict[str, Any]) -> None:
    client: TestClient = env["client"]
    app = env["app"]
    downloads: Path = env["downloads"]
    out: Path = env["out"]
    inp: Path = env["inp"]
    svc = app.state.service
    plat: RecordingPlatform = env["platform"]

    # ---- configure the watch through the API (loopback-only endpoints)
    r = client.put("/api/watch", json={"enabled": True, "folders": [
        {"path": str(downloads), "output": str(out)}]})
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["running"] and cfg["safety"]["auto_unlocked"]
    sid = cfg["folders"][0]["session_id"]
    pipe = svc.pipelines[sid]
    assert pipe.persistent and pipe.wait(180), "initial index of the output tree timed out"
    assert client.get(f"/api/sessions/{sid}").json()["kind"] == "watch"

    # ---- pick the downloads: clean corpus files (+ one code-project bundle)
    m = env["manifest"]
    clean = [e for e in m.entries if not (e.dup_of or e.near_dup_of or e.version_of
                                          or e.misleading_name)]
    by_cat: dict[str, list[Any]] = {}
    for e in clean:
        by_cat.setdefault(e.category, []).append(e)
    picks: list[Any] = []
    bundle = next((e for e in clean if e.kind == "code_project"), None)
    assert bundle is not None
    cats = sorted(c for c in by_cat if c != "Code")
    names: set[str] = set()
    while len(picks) < 15:
        for c in cats:
            pool = [e for e in by_cat[c] if e not in picks
                    and Path(e.rel_path).name.casefold() not in names]
            if pool and len(picks) < 15:
                picks.append(pool[0])
                names.add(Path(pool[0].rel_path).name.casefold())
    truth = {Path(e.rel_path).name: e.category for e in picks}
    truth[Path(bundle.rel_path).name] = "Code"
    examples = [p for p in sorted(out.rglob("*.pdf")) if p.is_file()][:2]
    assert len(examples) == 2

    finished: dict[str, float] = {}  # name -> wall time the download finished
    lock_released: dict[str, float] = {}
    moved_at: dict[str, float] = {}  # name -> first time it was seen gone from Downloads
    stop_monitor = threading.Event()

    def monitor() -> None:  # runs while downloads are still being written
        while not stop_monitor.is_set():
            now = time.time()
            for name in list(finished):
                if name not in moved_at and not (downloads / name).exists():
                    moved_at[name] = now
            time.sleep(0.02)

    mon = threading.Thread(target=monitor, daemon=True)
    mon.start()

    # 2 files held locked while "downloading"; released after the others are written
    locked = picks[:2]
    holders: list[contextlib.ExitStack] = []
    for e in locked:
        dst = downloads / Path(e.rel_path).name
        dst.write_bytes((inp / e.rel_path).read_bytes())
        stack = contextlib.ExitStack()
        stack.enter_context(held_lock(dst))
        holders.append(stack)
    lock_t0 = time.time()
    # 7 Chrome-style: chunks into name.crdownload, then rename to the final name
    for e in picks[2:9]:
        name = Path(e.rel_path).name
        part = downloads / f"{name}.crdownload"
        _write_chunks(part, (inp / e.rel_path).read_bytes())
        os.replace(part, downloads / name)
        finished[name] = time.time()
    # 6 written in place (Safari/Firefox-like final writes, or a copy from elsewhere)
    for e in picks[9:15]:
        name = Path(e.rel_path).name
        _write_chunks(downloads / name, (inp / e.rel_path).read_bytes())
        finished[name] = time.time()
    # a dropped code project: moves as one unit (never scattered)
    shutil.copytree(inp / bundle.rel_path, downloads / Path(bundle.rel_path).name)
    finished[Path(bundle.rel_path).name] = time.time()
    # exact duplicates of files already in the output tree -> pending Duplicate cards
    dupes = {f"copy of {p.name}": p for p in examples}
    for name, src in dupes.items():
        _write_chunks(downloads / name, src.read_bytes())
        finished[name] = time.time()
    # files nothing in the output tree resembles -> pending cards
    mystery = {"zq_notes.txt": "zebra quantum lullaby marmalade vortex",
               "kx_list.txt": "plinth gossamer turnip oscillate brine"}
    for name, text in mystery.items():
        _write_chunks(downloads / name, text.encode())
        finished[name] = time.time()
    # abandoned partial downloads: never processed
    _write_chunks(downloads / "big-video.mp4.crdownload", os.urandom(4096))
    (downloads / "archive.zip.part").write_bytes(os.urandom(2048))

    # release the locks (>= 2 s after they were taken: several settle periods)
    time.sleep(max(0.0, lock_t0 + 2.0 - time.time()))
    early_moves = [Path(e.rel_path).name for e in locked
                   if not (downloads / Path(e.rel_path).name).exists()]
    for e, stack in zip(locked, holders, strict=True):
        stack.close()
        name = Path(e.rel_path).name
        finished[name] = lock_released[name] = time.time()
    assert not early_moves, f"locked files were processed while locked: {early_moves}"
    assert plat.lock_checks > 0
    assert len(finished) == 20

    # ---- watch it happen: poll (no fixed sleeps) until all 20 are resolved
    deadline = time.time() + 120

    def resolved() -> dict[str, str]:
        state: dict[str, str] = {}
        with DB(svc.engine) as db:
            rows = db.exec(select(File).where(File.session_id == sid)).all()
            for f in rows:
                name = Path(f.rel_path).parts[0]
                if f.status == FileStatus.decided:
                    state[name] = "auto"
                elif f.status in (FileStatus.ready, FileStatus.skipped):
                    state[name] = "pending"
        return state

    state: dict[str, str] = {}
    while time.time() < deadline:
        state = resolved()
        if (len(state) >= 20 and all(n in state for n in finished)
                and pipe.incremental_idle.is_set()):
            break
        time.sleep(0.05)
    stop_monitor.set()
    mon.join(timeout=5)
    assert all(n in state for n in finished), (sorted(set(finished) - set(state)), state)

    auto = sorted(n for n, s in state.items() if s == "auto")
    pending = sorted(n for n, s in state.items() if s == "pending")
    latencies = {n: moved_at[n] - finished[n] for n in auto}
    print(f"\nM7: auto={len(auto)} pending={len(pending)} "
          f"latency max={max(latencies.values()):.2f}s "
          f"mean={sum(latencies.values()) / len(latencies):.2f}s")
    for n in auto:
        print(f"  {latencies[n]:5.2f}s  {n}")
    print(f"  pending: {pending}")

    # high-confidence files were auto-sorted, each within 10 s of finishing its download
    assert len(auto) >= 8, state
    assert max(latencies.values()) <= LATENCY_BOUND_S, latencies
    assert Path(bundle.rel_path).name in auto or Path(bundle.rel_path).name in pending
    # ... into the correct output folder (bundle as one unit)
    with DB(svc.engine) as db:
        decs = db.exec(select(Decision).where(Decision.session_id == sid,
                                              Decision.source == "auto")).all()
        assert len(decs) == len(auto)
        auto_dec: dict[str, int] = {}
        for d in decs:
            f = db.get(File, d.file_id)
            assert f is not None
            name = Path(f.rel_path).parts[0]
            auto_dec[name] = d.id or 0
            rel = Path(f.path).parent.relative_to(out.resolve()).as_posix()
            assert rel == truth.get(name), (name, rel, truth.get(name))
            if f.is_bundle:
                assert (Path(f.path) / "package.json").exists() or any(Path(f.path).iterdir())
    # duplicates and unplaceable files were not auto-moved; they're cards in the watch session
    for name in list(dupes) + list(mystery):
        assert state[name] == "pending", name
    cards = client.get(f"/api/sessions/{sid}/cards?limit=200").json()
    card_names = {c["file"]["name"] for c in cards}
    assert set(pending) <= card_names, (pending, card_names)
    assert {c["kind"] for c in cards if c["file"]["name"] in dupes} == {"duplicate"}
    # partial downloads never became files; nothing pre-existing was swept up
    with DB(svc.engine) as db:
        all_names = [Path(f.path).name for f in db.exec(select(File).where(
            File.session_id == sid)).all()]
    assert not [n for n in all_names if n.endswith((".crdownload", ".part"))]
    assert "old-report.pdf" not in all_names
    assert len(all_names) == 20, sorted(all_names)
    # notifications were sent with an undo link
    assert plat.notes and all(u and "/watch" in u for _t, _b, u in plat.notes)

    # ---- status + weekly digest
    st = client.get("/api/watch/status").json()
    assert st["pending_total"] == len(pending)
    act = client.get("/api/watch/activity?days=7").json()
    assert act["auto_moved"] == len(auto) and act["pending_total"] == len(pending)
    assert all(i["can_undo"] for i in act["items"])

    # ---- targeted undo refuses when a later change depends on it
    first = auto[0]
    with DB(svc.engine) as db:
        f0 = db.exec(select(File).where(File.session_id == sid,
                                        col(File.rel_path).startswith(first))).first()
        assert f0 is not None
        moved_path = Path(f0.path)
    ex = Executor(svc.engine, sid, downloads.resolve(), out.resolve(), platform=plat,
                  settings=svc.settings)
    elsewhere = ex.move(moved_path, out.resolve() / "Work" / "Resumes")  # user moves it again
    r = client.post(f"/api/decisions/{auto_dec[first]}/undo")
    assert r.status_code == 409, r.text
    assert "moved again" in r.json()["detail"]
    ex.undo(1)  # put it back where the auto-move left it
    assert moved_path.exists() and not elsewhere.exists()

    # ---- every auto-move is undoable, in any order (oldest first: not LIFO)
    originals = {n: downloads / n for n in auto}
    for name in sorted(auto, key=lambda n: auto_dec[n]):
        r = client.post(f"/api/decisions/{auto_dec[name]}/undo")
        assert r.status_code == 200, (name, r.text)
        assert originals[name].exists(), name
    for name in auto:
        src = (inp / next(e.rel_path for e in picks + [bundle]
                          if Path(e.rel_path).name == name))
        if src.is_file():
            assert _sha(originals[name]) == _sha(src), name
    assert client.post(f"/api/decisions/{auto_dec[auto[0]]}/undo").status_code == 400
    with DB(svc.engine) as db:
        ops = db.exec(select(Op).where(Op.session_id == sid)).all()
        assert not [o for o in ops if o.status == OpStatus.intent]
        for name in auto:
            dops = [o for o in ops if o.decision_id == auto_dec[name]]
            assert dops and all(o.status == OpStatus.undone for o in dops), name
            d = db.get(Decision, auto_dec[name])
            assert d is not None and d.undone

    # restored files are pending cards again, not re-added as new arrivals by the watcher
    t_end = time.time() + SETTLE_S * 6 + 2
    while time.time() < t_end:
        time.sleep(0.1)
    with DB(svc.engine) as db:
        rows = db.exec(select(File).where(File.session_id == sid)).all()
    assert len(rows) == 20
    assert all(f.status == FileStatus.ready for f in rows if Path(f.rel_path).parts[0] in auto)
    card_names = {c["file"]["name"]
                  for c in client.get(f"/api/sessions/{sid}/cards?limit=200").json()}
    assert set(auto) <= card_names


def test_watch_session_reused_across_restart(env: dict[str, Any], tmp_path: Path) -> None:
    """The watch session is created once per (folder, output) and resumed by a new daemon;
    files that arrived while it was down are caught up."""
    client: TestClient = env["client"]
    downloads: Path = env["downloads"]
    out: Path = env["out"]
    r = client.put("/api/watch", json={"enabled": True, "folders": [
        {"path": str(downloads), "output": str(out)}]})
    sid = r.json()["folders"][0]["session_id"]
    assert env["app"].state.service.pipelines[sid].wait(180)
    env["app"].state.watch.stop()
    for pipe in env["app"].state.service.pipelines.values():
        pipe.stop()
    (downloads / "while-down.txt").write_text("zebra quantum lullaby marmalade")

    # a new daemon reads the saved watch settings (home/watch.json) and resumes the session
    plat = RecordingPlatform()
    app2 = create_app(db_path=tmp_path / "t.db", settings=load_settings(), platform=plat, static_dir=None)
    s2 = app2.state.service.settings
    assert s2.watch.enabled and s2.watch.folders[0].path == str(downloads)
    s2.models.provider = "fake"
    s2.watch.settle_seconds, s2.watch.settle_poll_seconds = SETTLE_S, 0.1
    s2.proposals.enabled = False
    from swipesort.ml import get_provider

    app2.state.service.provider = get_provider(s2)
    app2.state.watch.start()
    try:
        assert app2.state.watch.folders[0].sid == sid
        deadline = time.time() + 60
        found = False
        while time.time() < deadline and not found:
            with DB(app2.state.service.engine) as db:
                found = db.exec(select(File).where(
                    File.session_id == sid, File.rel_path == "while-down.txt",
                    File.status == FileStatus.ready)).first() is not None
            time.sleep(0.1)
        assert found
    finally:
        app2.state.watch.stop()
        for pipe in app2.state.service.pipelines.values():
            pipe.stop()


def test_watch_endpoints_are_loopback_only(env: dict[str, Any]) -> None:
    app = env["app"]
    phone = TestClient(app, client=("192.168.1.20", 50123))
    for method, url in (("get", "/api/watch"), ("get", "/api/watch/status"),
                        ("get", "/api/watch/activity"), ("put", "/api/watch")):
        r = getattr(phone, method)(url, **({"json": {}} if method == "put" else {}))
        assert r.status_code in (401, 403), (url, r.status_code)


def test_put_watch_validates_folders(env: dict[str, Any], tmp_path: Path) -> None:
    client: TestClient = env["client"]
    r = client.put("/api/watch", json={"folders": [
        {"path": str(tmp_path / "nope"), "output": str(env["out"])}]})
    assert r.status_code == 400
    r = client.put("/api/watch", json={"folders": [
        {"path": str(env["downloads"]), "output": str(env["downloads"])}]})
    assert r.status_code == 400


def test_auto_move_locked_until_enough_decisions(env: dict[str, Any]) -> None:
    """SPEC §18 safety: auto-move stays off until the user made min_decisions_for_auto
    decisions (counted across all sessions, per file; automatic, undone and non-placement
    decisions don't count)."""
    client: TestClient = env["client"]
    svc = env["app"].state.service
    svc.settings.watch.min_decisions_for_auto = 5
    r = client.put("/api/watch", json={"enabled": True, "folders": [
        {"path": str(env["downloads"]), "output": str(env["out"])}]})
    sid = r.json()["folders"][0]["session_id"]
    assert svc.pipelines[sid].wait(180)
    with DB(svc.engine) as db:
        db.add(Decision(session_id=sid, action="confirm_all",
                        payload_json='{"members": [1, 2, 3], "kind": "m"}'))
        db.add(Decision(session_id=sid, action="mkdir"))
        db.add(Decision(session_id=sid, action="pick", source="auto",
                        payload_json='{"x": 1}'))
        db.add(Decision(session_id=sid, action="trash", undone=True, payload_json='{"x": 1}'))
        db.commit()
    safety = client.get("/api/watch/status").json()["safety"]
    assert safety == {"decisions": 3, "min_decisions_for_auto": 5, "auto_unlocked": False}

    src = next(e for e in env["manifest"].entries
               if e.category == "Finance/Invoices" and not e.dup_of)
    name = Path(src.rel_path).name
    _write_chunks(env["downloads"] / name, (env["inp"] / src.rel_path).read_bytes())
    deadline = time.time() + 30
    status = None
    while time.time() < deadline:
        with DB(svc.engine) as db:
            f = db.exec(select(File).where(File.session_id == sid,
                                           File.rel_path == name)).first()
            status = f.status if f is not None else None
        if status is not None and status != FileStatus.queued:
            break
        time.sleep(0.05)
    assert status == FileStatus.ready  # high confidence, but locked: a pending card
    assert (env["downloads"] / name).exists()
    assert client.get("/api/watch/status").json()["pending_total"] == 1
