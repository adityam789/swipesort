"""Plain-text extractor."""

from __future__ import annotations

from pathlib import Path

from swipesort.pipeline.extractors.base import ExtractResult

_TEXT_APPS = {
    "application/json", "application/xml", "application/x-yaml", "application/yaml",
    "application/toml", "application/x-sh", "application/javascript", "application/rtf",
    "application/x-ndjson", "application/sql",
}


def read_head(path: Path, max_chars: int) -> str:
    """First `max_chars` characters of a file, decoded as UTF-8 with errors replaced."""
    with path.open("rb") as f:
        raw = f.read(max(max_chars, 0) * 4 + 4)
    return raw.decode("utf-8", errors="replace").replace("\x00", "")[:max_chars]


class TextExtractor:
    def supports(self, mime: str) -> bool:
        return mime.startswith("text/") or mime in _TEXT_APPS or mime.endswith(("+json", "+xml"))

    def extract(self, path: Path, max_chars: int, pages: int) -> ExtractResult:
        text = read_head(path, max_chars)
        return ExtractResult(mime="text/plain", text=text)
