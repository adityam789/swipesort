"""Audio/video extractor: mutagen tags; ffprobe only if it is on PATH."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from swipesort.pipeline.extractors.base import ExtractResult


def _first(tags: Any, *keys: str) -> str:
    for k in keys:
        with contextlib.suppress(Exception):
            v = tags.get(k)
            if v:
                item = v[0] if isinstance(v, list | tuple) else v
                s = str(item).strip()
                if s:
                    return s
    return ""


def _ffprobe(path: Path) -> dict[str, Any]:
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    try:
        proc = subprocess.run(
            [exe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams",
             str(path)],
            capture_output=True, timeout=10, check=False,
        )
        data = json.loads(proc.stdout or b"{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    out: dict[str, Any] = {}
    fmt = data.get("format", {})
    try:
        out["duration"] = round(float(fmt["duration"]), 1)
    except (KeyError, TypeError, ValueError):
        pass
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and "width" in s:
            out["width"], out["height"] = int(s["width"]), int(s.get("height", 0))
            break
    return out


class MediaExtractor:
    def supports(self, mime: str) -> bool:
        return mime.startswith(("audio/", "video/"))

    def extract(self, path: Path, max_chars: int, pages: int) -> ExtractResult:
        import mutagen

        mime = "audio/unknown"
        meta: dict[str, Any] = {}
        try:
            f = mutagen.File(str(path), easy=True)
        except Exception:  # noqa: BLE001 - unreadable tags are not fatal
            f = None
        if f is not None:
            tags = f.tags
            if tags is not None:
                for key, names in (
                    ("title", ("title", "\xa9nam")),
                    ("artist", ("artist", "\xa9ART")),
                    ("album", ("album", "\xa9alb")),
                    ("genre", ("genre",)),
                    ("date", ("date", "year")),
                ):
                    v = _first(tags, *names)
                    if v:
                        meta[key] = v
            info = getattr(f, "info", None)
            length = getattr(info, "length", None)
            if length:
                meta["duration"] = round(float(length), 1)
        return ExtractResult(mime=mime, metadata=self._finish(path, meta))

    @staticmethod
    def _finish(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
        if "duration" not in meta or path.suffix.lower() in {".mp4", ".mov", ".m4v", ".mkv"}:
            for k, v in _ffprobe(path).items():
                meta.setdefault(k, v)
        meta["size_bytes"] = path.stat().st_size
        return meta
