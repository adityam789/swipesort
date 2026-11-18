"""Image extractor (Pillow, pillow-heif for HEIC). No OCR here: the pipeline calls platform.ocr."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from PIL import Image

from swipesort.pipeline.extractors.base import ExtractResult

_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825
_TAG_MAKE, _TAG_MODEL, _TAG_DATETIME = 0x010F, 0x0110, 0x0132
_TAG_DT_ORIGINAL, _TAG_DT_DIGITIZED = 0x9003, 0x9004

_heif_registered = False


def _register_heif() -> None:
    global _heif_registered
    if _heif_registered:
        return
    _heif_registered = True
    with contextlib.suppress(Exception):  # optional codec
        from pillow_heif import register_heif_opener

        register_heif_opener()


def _clean(v: object) -> str:
    return str(v).replace("\x00", "").strip()


def _exif_date(raw: str) -> str:
    """'2024:05:01 12:30:00' -> '2024-05-01T12:30:00'."""
    s = raw.strip()
    if len(s) >= 19 and s[4] == ":" and s[7] == ":":
        return f"{s[0:4]}-{s[5:7]}-{s[8:10]}T{s[11:19]}"
    return s


class ImageExtractor:
    def supports(self, mime: str) -> bool:
        return mime.startswith("image/") and mime != "image/svg+xml"

    def extract(self, path: Path, max_chars: int, pages: int) -> ExtractResult:
        _register_heif()
        meta: dict[str, Any] = {}
        mime = "image/unknown"
        with Image.open(path) as img:
            mime = Image.MIME.get(img.format or "", "image/unknown")
            meta["width"], meta["height"] = img.size
            if img.format:
                meta["format"] = img.format
            try:
                exif = img.getexif()
            except Exception:  # noqa: BLE001 - corrupt EXIF should not fail the image
                exif = None
            if exif:
                make = _clean(exif.get(_TAG_MAKE, ""))
                model = _clean(exif.get(_TAG_MODEL, ""))
                if make or model:
                    meta["camera"] = (
                        model if model.lower().startswith(make.lower()) else f"{make} {model}"
                    ).strip()
                sub = exif.get_ifd(_EXIF_IFD)
                raw_date = (
                    sub.get(_TAG_DT_ORIGINAL)
                    or sub.get(_TAG_DT_DIGITIZED)
                    or exif.get(_TAG_DATETIME)
                )
                if raw_date:
                    meta["exif_date"] = _exif_date(_clean(raw_date))
                meta["has_gps"] = bool(exif.get_ifd(_GPS_IFD))
        return ExtractResult(mime=mime, metadata=meta, is_image=True)
