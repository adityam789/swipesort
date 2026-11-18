"""FastAPI app factory + static PWA mount."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from swipesort.api.routes import router
from swipesort.api.service import SessionService
from swipesort.config import Settings, app_dir, get_settings
from swipesort.db.session import make_engine
from swipesort.platform.base import Platform

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(
    db_path: Path | str | None = None,
    settings: Settings | None = None,
    platform: Platform | None = None,
    static_dir: Path | None = FRONTEND_DIST,
) -> FastAPI:
    settings = settings or get_settings()
    home = app_dir()
    if db_path is None:
        home.mkdir(parents=True, exist_ok=True)
        db_path = home / "swipesort.db"
    app = FastAPI(title="SwipeSort", version="0.1.0")
    app.state.service = SessionService(make_engine(db_path), settings, platform)
    app.state.thumb_dir = home / "thumbs"
    app.include_router(router)
    if static_dir is not None and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="pwa")
    return app
