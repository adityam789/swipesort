"""M6 acceptance: a full session completed from a phone over LAN.

The phone is simulated by a TestClient whose peer address is a LAN IP. The session is created
from loopback (desktop); the phone pairs with a QR code, then fetches cards, decides them all,
undoes, loads previews (cookie auth) and streams events. The real-phone run is the manual
checklist in docs/user-guide.md §4.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from swipesort import net
from swipesort.config import Settings
from swipesort.main import create_app

PHONE = ("192.168.1.20", 50123)
OTHER = ("192.168.1.66", 50999)


def _settings() -> Settings:
    s = Settings()
    s.models.provider = "fake"
    s.batch.min_size = s.batch.inbox_min_size = 10**9
    s.proposals.enabled = False
    s.server.lan = True
    s.server.print_qr = False
    s.server.preview_downscale_bytes = 10_000  # exercise the downscaled-photo path
    return s


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(net, "lan_ip", lambda: "192.168.1.10")
    monkeypatch.setenv("SWIPESORT_HOME", str(tmp_path / "home"))
    return create_app(db_path=tmp_path / "t.db", settings=_settings(), static_dir=None)


def _corpus(tmp: Path) -> tuple[Path, Path]:
    inp, out = tmp / "in", tmp / "out"
    (out / "Finance").mkdir(parents=True)
    (out / "School").mkdir(parents=True)
    (out / "Finance" / "a.txt").write_text("invoice amount due acme corp net 30")
    (out / "School" / "l.txt").write_text("lecture notes week one syllabus exam")
    inp.mkdir()
    for i in range(4):
        (inp / f"inv{i}.txt").write_text(f"invoice {i} amount due initech payment")
        (inp / f"lec{i}.txt").write_text(f"lecture {i} slides homework quiz exam")
    Image.effect_noise((1200, 900), 64).convert("RGB").save(inp / "photo.png")
    return inp, out


def _pair(desk: TestClient, phone: TestClient) -> dict[str, Any]:
    start = desk.post("/api/pair/start").json()
    assert start["url"] == f"http://192.168.1.10:8765/pair?code={start['code']}"
    r = phone.post("/api/pair", json={"code": start["code"], "name": "Test phone"})
    assert r.status_code == 200, r.text
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "samesite=strict" in r.headers["set-cookie"].lower()
    out: dict[str, Any] = r.json()
    return out


def _decide_body(card: dict[str, Any]) -> dict[str, Any]:
    """What a swipe sends: the card's own edge (right = accept/pick, else inbox/trash)."""
    for d in ("right", "up", "down"):
        e = card["edges"].get(d)
        if e and e["action"] in {"accept", "pick", "inbox", "trash"}:
            return {"action": e["action"], "target_folder_id": e.get("target_folder_id")}
    return {"action": "trash"}


def test_full_session_from_a_paired_phone(app: Any, tmp_path: Path) -> None:
    inp, out = _corpus(tmp_path)
    desk = TestClient(app)
    phone = TestClient(app, client=PHONE)

    # unpaired phone: static/pairing endpoints only
    assert phone.get("/api/sessions").status_code == 401
    assert phone.get("/api/pair/status").json() == {
        "local": False, "paired": False, "lan": True, "device_id": None}
    assert phone.post("/api/pair/start").status_code == 401

    sid = desk.post("/api/sessions", json={"input_root": str(inp),
                                           "output_root": str(out)}).json()["id"]
    assert app.state.service.pipelines[sid].wait(60)

    paired = _pair(desk, phone)
    assert phone.get("/api/pair/status").json()["paired"] is True
    # desktop-only endpoints stay desktop-only even with a token
    assert phone.post("/api/sessions", json={"input_root": str(inp),
                                             "output_root": str(out)}).status_code == 403
    assert phone.get("/api/fs/browse").status_code == 403
    assert phone.get("/api/devices").status_code == 403
    assert phone.post("/api/pair/start").status_code == 403

    # session picker: the desktop's live session
    sessions = phone.get("/api/sessions").json()
    assert [s["id"] for s in sessions] == [sid] and sessions[0]["live"]

    # stream with cookie auth
    with phone.websocket_connect(f"/api/sessions/{sid}/stream") as ws:
        assert '"hello"' in ws.receive_text()

    cards = phone.get(f"/api/sessions/{sid}/cards?limit=50").json()
    assert len(cards) == 9
    photo = next(c for c in cards if c["file"]["name"] == "photo.png")
    pv = phone.get(f"/api/files/{photo['file']['id']}/preview")
    assert pv.status_code == 200 and pv.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(pv.content)) as im:
        assert max(im.size) <= 1600
    assert phone.get(f"/api/files/{cards[0]['file']['id']}/preview").status_code == 200

    # decide one, undo it, then everything to completion
    first = cards[0]
    body = _decide_body(first)
    assert phone.post(f"/api/cards/{first['id']}/decide", json=body).status_code == 200
    undo = phone.post(f"/api/sessions/{sid}/undo", json={"steps": 1})
    assert undo.status_code == 200, undo.text
    assert (inp / first["file"]["name"]).exists()

    deadline = time.monotonic() + 60
    while True:
        cards = phone.get(f"/api/sessions/{sid}/cards?limit=50").json()
        if not cards:
            break
        for c in cards:
            body = _decide_body(c)
            r = phone.post(f"/api/cards/{c['id']}/decide", json=body)
            assert r.status_code == 200, r.text
        assert time.monotonic() < deadline
    prog = phone.get(f"/api/sessions/{sid}").json()["progress"]
    assert prog["remaining"] == 0 and prog["decided"] == 9
    assert not any(p.is_file() for p in inp.iterdir())

    # bearer token works for non-browser clients
    bearer = TestClient(app, client=OTHER, headers={"Authorization": f"Bearer {paired['token']}"})
    assert bearer.get(f"/api/sessions/{sid}").status_code == 200


def test_ws_rejects_unpaired_and_accepts_query_token(app: Any, tmp_path: Path) -> None:
    inp, out = _corpus(tmp_path)
    desk = TestClient(app)
    sid = desk.post("/api/sessions", json={"input_root": str(inp),
                                           "output_root": str(out)}).json()["id"]
    stranger = TestClient(app, client=OTHER)
    with (pytest.raises(WebSocketDisconnect) as exc,
          stranger.websocket_connect(f"/api/sessions/{sid}/stream")):
        pass
    assert exc.value.code == 4401
    token = _pair(desk, TestClient(app, client=PHONE))["token"]
    with stranger.websocket_connect(f"/api/sessions/{sid}/stream?token={token}") as ws:
        assert '"hello"' in ws.receive_text()
    # ?token= is only for the WebSocket
    assert stranger.get(f"/api/sessions/{sid}?token={token}").status_code == 401


def test_revoked_device_is_rejected(app: Any) -> None:
    desk, phone = TestClient(app), TestClient(app, client=PHONE)
    dev = _pair(desk, phone)
    assert phone.get("/api/sessions").status_code == 200
    devices = desk.get("/api/devices").json()
    assert [d["name"] for d in devices] == ["Test phone"]
    assert desk.delete(f"/api/devices/{dev['device_id']}").status_code == 204
    assert phone.get("/api/sessions").status_code == 401
    assert desk.get("/api/devices").json() == []
    assert desk.delete(f"/api/devices/{dev['device_id']}").status_code == 404
    # only a hash of the token is stored
    raw = (Path(str(app.state.service.engine.url.database))).read_bytes()
    assert dev["token"].encode() not in raw


def test_code_is_single_use(app: Any) -> None:
    desk = TestClient(app)
    code = desk.post("/api/pair/start").json()["code"]
    assert TestClient(app, client=PHONE).post("/api/pair", json={"code": code}).status_code == 200
    r = TestClient(app, client=OTHER).post("/api/pair", json={"code": code})
    assert r.status_code == 403


def test_code_is_case_and_dash_insensitive(app: Any) -> None:
    code = TestClient(app).post("/api/pair/start").json()["code"]
    typed = f"{code[:4].lower()}-{code[4:]}"
    assert TestClient(app, client=PHONE).post("/api/pair", json={"code": typed}).status_code == 200


def test_expired_code_is_rejected(app: Any) -> None:
    mgr = app.state.pairing
    now = [1_000_000.0]
    mgr.clock = lambda: now[0]
    code = TestClient(app).post("/api/pair/start").json()["code"]
    now[0] += app.state.service.settings.server.pair_code_ttl_s + 1
    assert TestClient(app, client=PHONE).post("/api/pair", json={"code": code}).status_code == 403


def test_expired_token_is_rejected(app: Any) -> None:
    mgr = app.state.pairing
    now = [time.time()]
    mgr.clock = lambda: now[0]
    phone = TestClient(app, client=PHONE)
    _pair(TestClient(app), phone)
    assert phone.get("/api/sessions").status_code == 200
    now[0] += app.state.service.settings.server.token_ttl_days * 86400 + 1
    assert phone.get("/api/sessions").status_code == 401


def test_failed_attempts_are_rate_limited(app: Any) -> None:
    desk, phone = TestClient(app), TestClient(app, client=PHONE)
    code = desk.post("/api/pair/start").json()["code"]
    limit = app.state.service.settings.server.pair_max_failures
    for _ in range(limit):
        assert phone.post("/api/pair", json={"code": "WRONGWRONG"}).status_code == 403
    # locked out even with the right code; other clients are unaffected
    assert phone.post("/api/pair", json={"code": code}).status_code == 429
    assert TestClient(app, client=OTHER).post("/api/pair", json={"code": code}).status_code == 200


def test_global_failure_burst_voids_pending_codes(app: Any) -> None:
    cfg = app.state.service.settings.server
    code = TestClient(app).post("/api/pair/start").json()["code"]
    for i in range(cfg.pair_global_max_failures):
        TestClient(app, client=(f"10.0.0.{i + 1}", 1)).post("/api/pair", json={"code": "NOPE2345"})
    assert TestClient(app, client=PHONE).post("/api/pair", json={"code": code}).status_code == 403


def test_qr_and_local_guards(app: Any) -> None:
    desk = TestClient(app)
    code = desk.post("/api/pair/start").json()["code"]
    svg = desk.get(f"/api/pair/qr.svg?code={code}")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in svg.content
    assert desk.get("/api/pair/qr.svg?code=<script>").status_code == 400
    # DNS-rebinding guard: loopback with a foreign Host header is refused
    assert desk.get("/api/sessions", headers={"host": "evil.example:8765"}).status_code == 403
    assert desk.get("/api/sessions", headers={"host": "localhost:8765"}).status_code == 200
    assert desk.get("/api/pair/status").json()["local"] is True


def test_loopback_detection() -> None:
    assert net.is_loopback("127.0.0.1") and net.is_loopback("::1")
    assert net.is_loopback("::ffff:127.0.0.1")
    assert not net.is_loopback("192.168.1.20") and not net.is_loopback(None)
    assert not net.is_loopback("evil.example")
    assert net.host_name("[::1]:8765") == "[::1]" and net.host_name("LocalHost:1") == "localhost"
    ip = net.lan_ip()
    assert ip.count(".") == 3


def test_spa_fallback_serves_index_for_client_routes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>pwa</html>")
    (dist / "assets" / "a.js").write_text("x")
    (dist / "manifest.webmanifest").write_text("{}")
    a = create_app(db_path=tmp_path / "t.db", settings=_settings(), static_dir=dist)
    phone = TestClient(a, client=PHONE)  # static is public; only /api needs a token
    for path in ("/", "/pair?code=ABC", "/sort"):
        r = phone.get(path)
        assert r.status_code == 200 and "pwa" in r.text, path
        assert r.headers["cache-control"] == "no-cache"
    assert phone.get("/assets/a.js").text == "x"
    assert phone.get("/assets/missing.js").status_code == 404
    assert phone.get("/api/nope").status_code == 401
    assert TestClient(a).get("/api/nope").status_code == 404
