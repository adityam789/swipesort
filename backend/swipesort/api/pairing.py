"""Phone pairing over LAN + request authentication (SPEC §9, §10, §12).

Trust model:
- Loopback peers (127.0.0.1 / ::1) are the desktop: trusted as before, but their Host header
  must be a local name (DNS-rebinding guard).
- Every other peer must present a device token for /api/* and the WebSocket: an HttpOnly
  SameSite=Strict cookie set at pairing (so <img src> previews and the WS just work), an
  `Authorization: Bearer` header, or `?token=` on the WebSocket only.
- Unauthenticated LAN peers may only load the static PWA, `GET /api/pair/status` and
  `POST /api/pair`.
- Pairing codes are minted from loopback only, short-lived, single-use, compared in constant
  time, and failed redeems are rate limited per client (and globally).
- Device tokens are random 256-bit secrets; only their SHA-256 is stored (table `device`).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import segno
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine
from sqlmodel import Session as DB
from sqlmodel import select
from starlette.datastructures import Headers, QueryParams
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from swipesort import net
from swipesort.config import Server, Settings
from swipesort.db.models import Device

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: readable if typed by hand
CODE_RE = re.compile(rf"^[{CODE_ALPHABET}]{{4,32}}$")
AUTH_SCOPE_KEY = "swipesort.auth"


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def normalize_code(code: str) -> str:
    return re.sub(r"[\s-]", "", code).upper()


class PairError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Auth:
    local: bool
    device_id: int | None = None


# ---------- schemas ----------
class PairStatusOut(BaseModel):
    local: bool
    paired: bool
    lan: bool
    device_id: int | None = None


class PairStartOut(BaseModel):
    code: str
    url: str
    lan: bool
    lan_ip: str
    expires_at: datetime
    expires_in: int


class PairIn(BaseModel):
    code: str = Field(max_length=64)
    name: str = Field("", max_length=80)


class PairOut(BaseModel):
    device_id: int
    token: str  # also set as an HttpOnly cookie; returned for non-browser clients (Bearer)
    expires_at: datetime | None


class DeviceOut(BaseModel):
    id: int
    name: str
    user_agent: str
    created_at: datetime
    last_seen: datetime
    expires_at: datetime | None


# ---------- state ----------
class PairingManager:
    def __init__(self, engine: Engine, settings: Server,
                 clock: Callable[[], float] = time.time) -> None:
        self.engine = engine
        self.cfg = settings
        self.clock = clock
        self._codes: dict[str, float] = {}  # sha256(code) -> expiry (epoch s)
        self._fails: dict[str, list[float]] = {}
        self._seen: dict[int, float] = {}  # device id -> last persisted last_seen
        self._lock = threading.Lock()

    # codes
    def start(self) -> tuple[str, float]:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(self.cfg.pair_code_length))
        exp = self.clock() + self.cfg.pair_code_ttl_s
        with self._lock:
            self._prune()
            self._codes[_sha256(code)] = exp
        return code, exp

    def _prune(self) -> None:
        now = self.clock()
        self._codes = {h: e for h, e in self._codes.items() if e > now}
        window = now - self.cfg.pair_failure_window_s
        self._fails = {ip: kept for ip, ts in self._fails.items()
                       if (kept := [t for t in ts if t > window])}

    def _take_code(self, code: str) -> bool:
        """Constant-time match against every live code; a match is consumed (single use)."""
        h = _sha256(normalize_code(code))
        hit: str | None = None
        for k in list(self._codes):
            if hmac.compare_digest(h, k):
                hit = k
        if hit is None:
            return False
        del self._codes[hit]
        return True

    def redeem(self, code: str, client: str, name: str = "", user_agent: str = ""
               ) -> tuple[str, Device]:
        with self._lock:
            self._prune()
            if len(self._fails.get(client, [])) >= self.cfg.pair_max_failures:
                raise PairError(429, "too many failed pairing attempts; try again later")
            if not self._take_code(code):
                self._fails.setdefault(client, []).append(self.clock())
                if sum(map(len, self._fails.values())) >= self.cfg.pair_global_max_failures:
                    self._codes.clear()  # under attack: void every pending code
                raise PairError(403, "invalid or expired pairing code")
        token = secrets.token_urlsafe(32)
        now = datetime.fromtimestamp(self.clock(), UTC)
        ttl = self.cfg.token_ttl_days
        dev = Device(token_hash=_sha256(token), name=name.strip()[:80] or "Phone",
                     user_agent=user_agent[:300], created_at=now, last_seen=now,
                     expires_at=now + timedelta(days=ttl) if ttl > 0 else None)
        with DB(self.engine) as db:
            db.add(dev)
            db.commit()
            db.refresh(dev)
        return token, dev

    # tokens
    def verify(self, token: str | None) -> int | None:
        if not token or len(token) > 256:
            return None
        h = _sha256(token)
        with DB(self.engine) as db:
            dev = db.exec(select(Device).where(Device.token_hash == h)).first()
            if dev is None or dev.id is None:
                return None
            now = self.clock()
            if dev.expires_at is not None and _aware(dev.expires_at).timestamp() <= now:
                return None
            if now - self._seen.get(dev.id, 0.0) >= self.cfg.last_seen_every_s:
                self._seen[dev.id] = now
                dev.last_seen = datetime.fromtimestamp(now, UTC)
                db.add(dev)
                db.commit()
            return dev.id

    def devices(self) -> list[DeviceOut]:
        with DB(self.engine) as db:
            rows = db.exec(select(Device).order_by(Device.created_at)).all()  # type: ignore[arg-type]
            return [DeviceOut(id=d.id or 0, name=d.name, user_agent=d.user_agent,
                              created_at=_aware(d.created_at), last_seen=_aware(d.last_seen),
                              expires_at=_aware(d.expires_at) if d.expires_at else None)
                    for d in rows]

    def revoke(self, device_id: int) -> bool:
        with DB(self.engine) as db:
            dev = db.get(Device, device_id)
            if dev is None:
                return False
            db.delete(dev)
            db.commit()
        self._seen.pop(device_id, None)
        return True


# ---------- middleware ----------
def _token(scope: Scope, cookie_name: str) -> str | None:
    headers = Headers(scope=scope)
    authz = headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        return authz[7:].strip()
    for part in headers.get("cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == cookie_name and v:
            return v
    if scope["type"] == "websocket":  # browsers can't set headers on a WebSocket
        return QueryParams(scope.get("query_string", b"").decode()).get("token")
    return None


PUBLIC = {("GET", "/api/pair/status"), ("POST", "/api/pair")}


class AuthMiddleware:
    def __init__(self, app: ASGIApp, pairing: PairingManager, settings: Server) -> None:
        self.app = app
        self.pairing = pairing
        self.cfg = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        is_api = path == "/api" or path.startswith("/api/")
        client = scope.get("client")
        local = net.is_loopback(client[0] if client else None)
        if local:
            host = net.host_name(Headers(scope=scope).get("host", ""))
            if is_api and host not in {h.lower() for h in self.cfg.local_hostnames}:
                await self._reject(scope, receive, send, 403, "unexpected Host header")
                return
            scope[AUTH_SCOPE_KEY] = Auth(local=True)
            await self.app(scope, receive, send)
            return
        device = self.pairing.verify(_token(scope, self.cfg.token_cookie)) if is_api else None
        scope[AUTH_SCOPE_KEY] = Auth(local=False, device_id=device)
        method = "GET" if scope["type"] == "websocket" else scope.get("method", "GET")
        if is_api and device is None and (method, path.rstrip("/")) not in PUBLIC:
            await self._reject(scope, receive, send, 401, "pair this device first")
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int,
                      detail: str) -> None:
        if scope["type"] == "websocket":
            await WebSocketClose(code=4000 + status)(scope, receive, send)
        else:
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)


def auth_of(conn: HTTPConnection) -> Auth:
    a = conn.scope.get(AUTH_SCOPE_KEY)
    if isinstance(a, Auth):
        return a
    return Auth(local=net.is_loopback(conn.client.host if conn.client else None))


def require_local(request: Request) -> None:
    """Dependency: desktop-only endpoints (pairing, devices, filesystem paths)."""
    if not auth_of(request).local:
        raise HTTPException(403, "only available on the computer running SwipeSort")


# ---------- routes ----------
router = APIRouter(prefix="/api")


def _mgr(request: Request) -> PairingManager:
    m: PairingManager = request.app.state.pairing
    return m


def _settings(request: Request) -> Settings:
    s: Settings = request.app.state.service.settings
    return s


def pair_url(settings: Settings, code: str, ip: str | None = None) -> str:
    return f"http://{ip or net.lan_ip()}:{settings.server.port}/pair?code={code}"


def qr_svg(data: str) -> bytes:
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=6, border=2, dark="#000",
                                     light="#fff", xmldecl=False)
    return buf.getvalue()


def print_pairing_qr(url: str, expires_in: int) -> None:
    """ASCII QR in the terminal (`swipesort serve --lan`, `swipesort pair`)."""
    segno.make(url, error="m").terminal(compact=True)
    print(f"Scan with your phone, or open {url}  (expires in {expires_in // 60} min)")


@router.get("/pair/status", response_model=PairStatusOut)
def pair_status(request: Request) -> PairStatusOut:
    a = auth_of(request)
    return PairStatusOut(local=a.local, paired=a.local or a.device_id is not None,
                         lan=_settings(request).server.lan, device_id=a.device_id)


@router.post("/pair/start", response_model=PairStartOut, dependencies=[Depends(require_local)])
def pair_start(request: Request) -> PairStartOut:
    s = _settings(request)
    code, exp = _mgr(request).start()
    ip = net.lan_ip()
    return PairStartOut(code=code, url=pair_url(s, code, ip), lan=s.server.lan, lan_ip=ip,
                        expires_at=datetime.fromtimestamp(exp, UTC),
                        expires_in=s.server.pair_code_ttl_s)


@router.get("/pair/qr.svg", dependencies=[Depends(require_local)])
def pair_qr(request: Request, code: str = Query(..., max_length=32)) -> Response:
    if not CODE_RE.match(code):
        raise HTTPException(400, "bad code")
    return Response(qr_svg(pair_url(_settings(request), code)), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@router.post("/pair", response_model=PairOut)
def pair(body: PairIn, request: Request, response: Response) -> PairOut:
    s = _settings(request).server
    client = request.client.host if request.client else "?"
    try:
        token, dev = _mgr(request).redeem(body.code, client, body.name,
                                          request.headers.get("user-agent", ""))
    except PairError as e:
        raise HTTPException(e.status, str(e)) from e
    assert dev.id is not None
    response.set_cookie(s.token_cookie, token, httponly=True, samesite="strict", path="/",
                        max_age=s.token_ttl_days * 86400 if s.token_ttl_days > 0 else 10 * 365 * 86400)
    return PairOut(device_id=dev.id, token=token,
                   expires_at=_aware(dev.expires_at) if dev.expires_at else None)


@router.get("/devices", response_model=list[DeviceOut], dependencies=[Depends(require_local)])
def devices(request: Request) -> list[DeviceOut]:
    return _mgr(request).devices()


@router.delete("/devices/{device_id}", status_code=204, dependencies=[Depends(require_local)])
def revoke(device_id: int, request: Request) -> Response:
    if not _mgr(request).revoke(device_id):
        raise HTTPException(404, "no such device")
    return Response(status_code=204)
