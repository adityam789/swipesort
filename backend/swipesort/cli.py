from __future__ import annotations

import typer
import uvicorn

from swipesort.config import get_settings

app = typer.Typer(help="SwipeSort daemon")


@app.callback()
def _root() -> None:
    """SwipeSort command line."""


@app.command()
def serve(dev: bool = typer.Option(False, "--dev", help="auto-reload"),
          host: str | None = None, port: int | None = None) -> None:
    """Run the local daemon (serves the API and the built PWA)."""
    s = get_settings().server
    uvicorn.run("swipesort.main:create_app", factory=True, host=host or s.host,
                port=port or s.port, reload=dev)


@app.command()
def purge(session_id: int, older_than_days: int | None = None) -> None:
    """Permanently delete quarantined trash (the only hard-delete path)."""
    from pathlib import Path

    from sqlmodel import Session as DB

    from swipesort.config import app_dir
    from swipesort.db.models import Session as SessionRow
    from swipesort.db.session import make_engine
    from swipesort.exec.executor import Executor

    eng = make_engine(app_dir() / "swipesort.db")
    with DB(eng) as db:
        row = db.get(SessionRow, session_id)
        if row is None:
            raise typer.BadParameter(f"no session {session_id}")
        ex = Executor(eng, session_id, Path(row.input_root), Path(row.output_root))
        typer.echo(f"purged {ex.purge_trash(older_than_days)} item(s)")


if __name__ == "__main__":
    app()
