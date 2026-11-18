"""XLSX extractor (openpyxl, read-only): sheet names + first rows of each sheet."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from swipesort.pipeline.extractors.base import ExtractResult
from swipesort.pipeline.mime import XLSX

HEADER_ROWS = 5
MAX_SHEETS = 20


class XlsxExtractor:
    def supports(self, mime: str) -> bool:
        return mime == XLSX

    def extract(self, path: Path, max_chars: int, pages: int) -> ExtractResult:
        load_workbook = importlib.import_module("openpyxl").load_workbook
        wb = load_workbook(str(path), read_only=True, data_only=True)
        try:
            names = list(wb.sheetnames)
            parts: list[str] = []
            total = 0
            for ws in wb.worksheets[:MAX_SHEETS]:
                lines = [f"Sheet: {ws.title}"]
                for row in ws.iter_rows(max_row=HEADER_ROWS, values_only=True):
                    cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                    if cells:
                        lines.append(" | ".join(cells))
                chunk = "\n".join(lines)
                parts.append(chunk)
                total += len(chunk) + 1
                if total >= max_chars:
                    break
            meta: dict[str, Any] = {"sheets": names[:MAX_SHEETS]}
            props = wb.properties
            if props is not None:
                if props.title:
                    meta["title"] = str(props.title).strip()
                if props.creator:
                    meta["author"] = str(props.creator).strip()
                if props.created:
                    meta["created"] = props.created.replace(microsecond=0).isoformat()
        finally:
            wb.close()
        return ExtractResult(
            mime=XLSX, text="\n".join(parts)[:max_chars], metadata=meta, page_count=len(names)
        )
