"""DOCX extractor (python-docx)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from swipesort.pipeline.extractors.base import ExtractResult
from swipesort.pipeline.mime import DOCX


class DocxExtractor:
    def supports(self, mime: str) -> bool:
        return mime == DOCX

    def extract(self, path: Path, max_chars: int, pages: int) -> ExtractResult:
        from docx import Document

        doc = Document(str(path))
        parts: list[str] = []
        total = 0
        for p in doc.paragraphs:
            t = p.text.strip()
            if t:
                parts.append(t)
                total += len(t) + 1
                if total >= max_chars:
                    break
        if total < max_chars:
            for table in doc.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        line = " | ".join(cells)
                        parts.append(line)
                        total += len(line) + 1
                    if total >= max_chars:
                        break
                if total >= max_chars:
                    break
        meta: dict[str, Any] = {}
        cp = doc.core_properties
        if cp.title:
            meta["title"] = cp.title.strip()
        if cp.author:
            meta["author"] = cp.author.strip()
        if cp.created:
            meta["created"] = cp.created.replace(microsecond=0).isoformat()
        return ExtractResult(mime=DOCX, text="\n".join(parts)[:max_chars], metadata=meta)
