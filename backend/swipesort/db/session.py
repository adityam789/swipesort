from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from swipesort.db import models  # noqa: F401  (register tables)

# Bump whenever a table/column changes. Session state is short-lived, so instead of migrations
# an outdated database file is moved aside (never deleted) and a fresh one is created.
SCHEMA_VERSION = 6


def _version(path: Path) -> int:
    con = sqlite3.connect(path)
    try:
        return int(con.execute("PRAGMA user_version").fetchone()[0])
    finally:
        con.close()


def make_engine(db_path: Path | str) -> Engine:
    memory = str(db_path) == ":memory:"
    if not memory:
        path = Path(db_path)
        if path.exists() and path.stat().st_size > 0 and _version(path) != SCHEMA_VERSION:
            backup = path.with_name(f"{path.name}.v{_version(path)}.bak")
            for suffix in ("", "-wal", "-shm"):
                src = Path(str(path) + suffix)
                if src.exists():
                    src.replace(Path(str(backup) + suffix))
    url = "sqlite://" if memory else f"sqlite:///{Path(db_path)}"
    eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(eng, "connect")
    def _pragmas(conn, _rec):  # type: ignore[no-untyped-def]
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return eng


def session_scope(engine: Engine) -> Iterator[DBSession]:
    with DBSession(engine) as s:
        yield s
