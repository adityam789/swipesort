from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, PlainTextResponse, Response
from PIL import Image, ImageOps

from swipesort.api.pairing import require_local
from swipesort.api.schemas import (
    BrowseEntry,
    BrowseOut,
    Card,
    DecideIn,
    DecideOut,
    FolderCreate,
    FolderOut,
    SessionCreate,
    SessionOut,
    StreamEvent,
    TreeNode,
    UndoIn,
    UndoOut,
    WatchActivity,
    WatchConfigIn,
    WatchConfigOut,
    WatchStatus,
)
from swipesort.api.service import BadRequest, NotFound, SessionService
from swipesort.exec.errors import ExecError
from swipesort.watch.service import WatchService

router = APIRouter(prefix="/api")

IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
TEXT_EXT = {"txt", "md", "csv", "json", "py", "js", "ts", "html", "css", "log", "toml", "yaml", "yml"}


def svc(request: Request) -> SessionService:
    s: SessionService = request.app.state.service
    return s


def _guard(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(404, str(exc))
    if isinstance(exc, (BadRequest, ExecError)):
        return HTTPException(409 if isinstance(exc, ExecError) else 400, str(exc))
    raise exc


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(request: Request) -> list[SessionOut]:
    """Newest first; `live` sessions belong to this daemon run (a phone resumes one)."""
    return svc(request).list_sessions()


# Creating a session names arbitrary filesystem roots: desktop (loopback) only.
@router.post("/sessions", response_model=SessionOut, dependencies=[Depends(require_local)])
def create_session(body: SessionCreate, request: Request) -> SessionOut:
    try:
        return svc(request).create_session(body.input_root, body.output_root)
    except Exception as e:
        raise _guard(e) from e


@router.get("/sessions/{sid}", response_model=SessionOut)
def get_session(sid: int, request: Request) -> SessionOut:
    try:
        return svc(request).get_session(sid)
    except Exception as e:
        raise _guard(e) from e


@router.get("/sessions/{sid}/tree", response_model=TreeNode)
def tree(sid: int, request: Request, side: str = Query("input")) -> TreeNode:
    try:
        return svc(request).tree(sid, side)
    except Exception as e:
        raise _guard(e) from e


@router.get("/sessions/{sid}/folders", response_model=list[FolderOut])
def folders(sid: int, request: Request) -> list[FolderOut]:
    try:
        return svc(request).list_folders(sid)
    except Exception as e:
        raise _guard(e) from e


@router.get("/sessions/{sid}/cards", response_model=list[Card])
def cards(sid: int, request: Request, limit: int = Query(20, ge=1, le=200)) -> list[Card]:
    try:
        return svc(request).cards(sid, limit)
    except Exception as e:
        raise _guard(e) from e


@router.post("/cards/{card_id}/decide", response_model=DecideOut)
def decide(card_id: str, body: DecideIn, request: Request) -> DecideOut:
    try:
        return svc(request).decide(card_id, body)
    except Exception as e:
        raise _guard(e) from e


@router.post("/proposals/{pid}/decide", response_model=DecideOut)
def decide_proposal(pid: int, body: DecideIn, request: Request) -> DecideOut:
    """SPEC §12: {action: accept|rename|dismiss|inbox, name?, accepted_nodes?, renames?}.
    The same proposal is also decidable as card "p<pid>" via /api/cards/{id}/decide."""
    try:
        return svc(request).decide_proposal(pid, body)
    except Exception as e:
        raise _guard(e) from e


@router.post("/sessions/{sid}/undo", response_model=UndoOut)
def undo(sid: int, body: UndoIn, request: Request) -> UndoOut:
    try:
        return svc(request).undo(sid, max(1, body.steps))
    except Exception as e:
        raise _guard(e) from e


@router.post("/decisions/{decision_id}/undo", response_model=UndoOut)
def undo_decision(decision_id: int, request: Request) -> UndoOut:
    """Targeted undo of one decision (watch mode's per-item Undo). 409 when later changes
    depend on it (e.g. the file was moved again)."""
    try:
        return svc(request).undo_decision(decision_id)
    except Exception as e:
        raise _guard(e) from e


# ---------- watch mode (SPEC §18): names filesystem paths -> desktop (loopback) only ----------
def watch_svc(request: Request) -> WatchService:
    w: WatchService = request.app.state.watch
    return w


@router.get("/watch", response_model=WatchConfigOut, dependencies=[Depends(require_local)])
def get_watch(request: Request) -> WatchConfigOut:
    return watch_svc(request).config()


@router.put("/watch", response_model=WatchConfigOut, dependencies=[Depends(require_local)])
def put_watch(body: WatchConfigIn, request: Request) -> WatchConfigOut:
    try:
        return watch_svc(request).update(body)
    except Exception as e:
        raise _guard(e) from e


@router.get("/watch/status", response_model=WatchStatus, dependencies=[Depends(require_local)])
def watch_status(request: Request) -> WatchStatus:
    return watch_svc(request).status()


@router.get("/watch/activity", response_model=WatchActivity,
            dependencies=[Depends(require_local)])
def watch_activity(request: Request, days: int | None = Query(None, ge=1, le=365)
                   ) -> WatchActivity:
    """The digest: auto-sorted items in the last `days` (default watch.digest_days)."""
    return watch_svc(request).activity(days)


@router.post("/folders", response_model=FolderOut)
def create_folder(body: FolderCreate, request: Request) -> FolderOut:
    try:
        return svc(request).create_folder(body.session_id, body.parent_id, body.name)
    except Exception as e:
        raise _guard(e) from e


@router.get("/files/{fid}/preview")
def preview(fid: int, request: Request) -> Response:
    try:
        path = svc(request).file_path(fid)
    except Exception as e:
        raise _guard(e) from e
    return _preview(path, f"f{fid}", request)


@router.get("/indexed/{ixid}/preview")
def preview_indexed(ixid: int, request: Request) -> Response:
    try:
        path = svc(request).indexed_path(ixid)
    except Exception as e:
        raise _guard(e) from e
    return _preview(path, f"ix{ixid}", request)


def _preview(path: Path, cache_key: str, request: Request) -> Response:
    s = svc(request)
    if path.is_symlink() or not path.exists():
        raise HTTPException(404, "missing")
    ext = path.suffix.lower().lstrip(".")
    if path.is_file() and ext in IMAGE_EXT:
        small = _downscaled(path, request.app.state.thumb_dir / cache_key, s)
        if small is not None:
            return FileResponse(small, media_type="image/jpeg")
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0])
    if path.is_file() and ext in TEXT_EXT:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return PlainTextResponse(f.read(2000))
    if path.is_file():
        cache = request.app.state.thumb_dir / cache_key
        cache.mkdir(parents=True, exist_ok=True)
        thumb = s.platform.thumbnail(path, cache)
        if thumb is not None and thumb.exists():
            return FileResponse(thumb)
    return Response(status_code=204)


def _downscaled(path: Path, cache: Path, s: SessionService) -> Path | None:
    """Large photos are shrunk (cached JPEG) so previews load fast on a phone over Wi-Fi."""
    cfg = s.settings.server
    try:
        st = path.stat()
        if st.st_size < cfg.preview_downscale_bytes or path.suffix.lower() == ".gif":
            return None
        out = cache / f"preview-{cfg.preview_max_px}-{st.st_mtime_ns}-{st.st_size}.jpg"
        if out.is_file():
            return out
        with Image.open(path) as im:
            img = ImageOps.exif_transpose(im)
            img.thumbnail((cfg.preview_max_px, cfg.preview_max_px))
            cache.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            img.convert("RGB").save(tmp, "JPEG", quality=cfg.preview_jpeg_quality)
            tmp.replace(out)  # app cache dir, not a user file
        return out
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


@router.get("/fs/browse", response_model=BrowseOut, dependencies=[Depends(require_local)])
def browse(path: str = Query("")) -> BrowseOut:
    p = Path(path).expanduser() if path else Path.home()
    if not p.is_absolute() or not p.is_dir():
        raise HTTPException(400, "not a directory")
    entries: list[BrowseEntry] = []
    try:
        for e in sorted(p.iterdir(), key=lambda x: x.name.casefold()):
            if e.name.startswith(".") or e.is_symlink():
                continue
            if e.is_dir():
                entries.append(BrowseEntry(name=e.name, path=str(e), is_dir=True))
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    parent = str(p.parent) if p.parent != p else None
    return BrowseOut(path=str(p), parent=parent, entries=entries)


@router.websocket("/sessions/{sid}/stream")
async def stream(ws: WebSocket, sid: int) -> None:
    service: SessionService = ws.app.state.service
    try:
        progress = service.get_session(sid).progress
    except NotFound:
        await ws.close(code=4404)
        return
    await ws.accept()
    q = service.events.subscribe(sid)
    try:
        await ws.send_text(StreamEvent(type="hello", progress=progress).model_dump_json())
        while True:
            await ws.send_text(await q.get())
    except WebSocketDisconnect:
        pass
    finally:
        service.events.unsubscribe(sid, q)
