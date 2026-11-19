"""FastAPI app factory + static PWA mount."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from swipesort import net
from swipesort.api import pairing
from swipesort.api.routes import router
from swipesort.api.service import SessionService
from swipesort.config import Settings, app_dir, get_settings
from swipesort.db.session import make_engine
from swipesort.ml.gateway import Provider
from swipesort.ml.llm import LLM
from swipesort.platform.base import Platform
from swipesort.watch.service import WatchService

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
# Must be revalidated on every load so a rebuilt PWA (new hashed assets) is picked up.
NO_CACHE = {"index.html", "sw.js", "registerSW.js", "manifest.webmanifest"}


class SPAStaticFiles(StaticFiles):
    """Built PWA; unknown client routes (/pair, /sort, ...) fall back to index.html."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        name = path.rsplit("/", 1)[-1]
        try:
            resp = await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or path.startswith("api") or "." in name:
                raise
            name = "index.html"
            resp = await super().get_response(name, scope)
        if name in NO_CACHE or name == "" or name == ".":
            resp.headers["Cache-Control"] = "no-cache"
        return resp


def create_app(
    db_path: Path | str | None = None,
    settings: Settings | None = None,
    platform: Platform | None = None,
    provider: Provider | None = None,
    llm: LLM | None = None,
    static_dir: Path | None = FRONTEND_DIST,
) -> FastAPI:
    settings = settings or get_settings()
    home = app_dir()
    if db_path is None:
        home.mkdir(parents=True, exist_ok=True)
        db_path = home / "swipesort.db"
    engine = make_engine(db_path)
    pairing_mgr = pairing.PairingManager(engine, settings.server)

    service = SessionService(engine, settings, platform, provider, llm)
    watch = WatchService(service, home=home)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.server.lan and settings.server.print_qr:
            code, _exp = pairing_mgr.start()
            pairing.print_pairing_qr(pairing.pair_url(settings, code, net.lan_ip()),
                                     settings.server.pair_code_ttl_s)
        if settings.watch.enabled:  # `serve --watch` / watch.enabled (SPEC §18)
            watch.start()
        try:
            yield
        finally:
            watch.stop()

    app = FastAPI(title="SwipeSort", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.state.watch = watch
    app.state.pairing = pairing_mgr
    app.state.thumb_dir = home / "thumbs"
    app.add_middleware(pairing.AuthMiddleware, pairing=pairing_mgr, settings=settings.server)
    app.include_router(pairing.router)
    app.include_router(router)
    if static_dir is not None and static_dir.is_dir():
        app.mount("/", SPAStaticFiles(directory=static_dir, html=True), name="pwa")
    return app
