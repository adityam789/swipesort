from __future__ import annotations

import os

import typer
import uvicorn

from swipesort.config import get_settings

app = typer.Typer(help="SwipeSort daemon")


@app.callback()
def _root() -> None:
    """SwipeSort command line."""


@app.command()
def serve(dev: bool = typer.Option(False, "--dev", help="auto-reload"),
          lan: bool = typer.Option(False, "--lan",
                                   help="listen on the LAN so a paired phone can connect"),
          host: str | None = None, port: int | None = None) -> None:
    """Run the local daemon (serves the API and the built PWA).

    Default is localhost-only. With --lan it binds server.lan_host (0.0.0.0) and prints a
    pairing QR; on Windows the first LAN bind triggers a Windows Firewall prompt (allow it on
    private networks)."""
    settings = get_settings()
    s = settings.server
    lan = lan or s.lan
    if port is not None:
        os.environ["SWIPESORT_PORT"] = str(port)
    os.environ["SWIPESORT_LAN"] = "1" if lan else "0"  # for uvicorn's reload child process
    s.lan, s.port = lan, port or s.port  # this process (create_app reads the singleton)
    bind = host or (s.lan_host if lan else s.host)
    if lan:
        typer.echo("LAN mode: phones on this network can pair. On Windows, allow SwipeSort in "
                   "the Windows Firewall prompt (private networks only).")
    uvicorn.run("swipesort.main:create_app", factory=True, host=bind,
                port=port or s.port, reload=dev)


@app.command()
def pair(port: int | None = None) -> None:
    """Print a fresh phone-pairing QR from the running daemon."""
    import httpx

    from swipesort.api.pairing import print_pairing_qr

    p = port or get_settings().server.port
    try:
        r = httpx.post(f"http://127.0.0.1:{p}/api/pair/start", timeout=5)
        r.raise_for_status()
    except httpx.HTTPError as e:
        typer.echo(f"daemon not reachable on port {p}: {e}", err=True)
        raise typer.Exit(1) from e
    body = r.json()
    if not body["lan"]:
        typer.echo("The daemon is localhost-only. Restart it with: swipesort serve --lan")
    print_pairing_qr(body["url"], int(body["expires_in"]))


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
