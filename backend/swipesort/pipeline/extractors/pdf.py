"""PDF extractor (pypdfium2; Apache/BSD, not PyMuPDF)."""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

from swipesort.pipeline.extractors.base import ExtractResult
from swipesort.pipeline.mime import PDF

_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?")


def parse_pdf_date(raw: str) -> str:
    """'D:20250314093000+01'00'' -> '2025-03-14T09:30:00' (best effort, '' if unparseable)."""
    m = _PDF_DATE.match(raw.strip())
    if not m:
        return ""
    y, mo, d, h, mi, s = (g or None for g in m.groups())
    out = f"{y}-{mo or '01'}-{d or '01'}"
    if h:
        out += f"T{h}:{mi or '00'}:{s or '00'}"
    return out


class PdfExtractor:
    def supports(self, mime: str) -> bool:
        return mime == PDF

    def extract(self, path: Path, max_chars: int, pages: int) -> ExtractResult:
        pdfium: Any = importlib.import_module("pypdfium2")
        doc = pdfium.PdfDocument(str(path))
        try:
            page_count = len(doc)
            meta: dict[str, Any] = {}
            raw_meta = doc.get_metadata_dict()
            for key, out in (("Title", "title"), ("Author", "author"), ("Subject", "subject")):
                val = str(raw_meta.get(key, "")).strip()
                if val:
                    meta[out] = val
            created = parse_pdf_date(str(raw_meta.get("CreationDate", "")))
            if created:
                meta["created"] = created
            chunks: list[str] = []
            total = 0
            for i in range(min(page_count, max(pages, 0))):
                page = doc[i]
                try:
                    tp = page.get_textpage()
                    try:
                        chunks.append(tp.get_text_range())
                    finally:
                        tp.close()
                finally:
                    page.close()
                total += len(chunks[-1])
                if total >= max_chars:
                    break
        finally:
            doc.close()
        text = "\n".join(c.strip() for c in chunks if c.strip())[:max_chars]
        return ExtractResult(mime=PDF, text=text, metadata=meta, page_count=page_count)
