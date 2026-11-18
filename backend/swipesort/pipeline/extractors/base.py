"""Extractor contract (CLAUDE.md: Extractor.supports(mime) / extract(path) -> Extraction)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ExtractResult(BaseModel):
    mime: str
    text: str = ""  # extracted text from first `pages` pages/slides/sheet headers, capped
    metadata: dict[str, Any] = Field(default_factory=dict)  # title, author, exif_date, camera…
    page_count: int = 0
    ocr_used: bool = False
    is_image: bool = False


class Extractor(Protocol):
    def supports(self, mime: str) -> bool: ...
    def extract(self, path: Path, max_chars: int, pages: int) -> ExtractResult: ...
