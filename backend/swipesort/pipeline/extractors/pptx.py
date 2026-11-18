"""PPTX extractor (python-pptx): slide titles + text of the first slides."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from swipesort.pipeline.extractors.base import ExtractResult
from swipesort.pipeline.mime import PPTX

SLIDES_PER_PAGE = 3


def _shape_texts(shapes: Any) -> list[str]:
    out: list[str] = []
    for shape in shapes:
        if getattr(shape, "has_text_frame", False):
            t = str(shape.text_frame.text).strip()
            if t:
                out.append(t)
        elif getattr(shape, "shapes", None) is not None:  # group shape
            out.extend(_shape_texts(shape.shapes))
        elif getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                cells = [str(c.text).strip() for c in row.cells if str(c.text).strip()]
                if cells:
                    out.append(" | ".join(cells))
    return out


class PptxExtractor:
    def supports(self, mime: str) -> bool:
        return mime == PPTX

    def extract(self, path: Path, max_chars: int, pages: int) -> ExtractResult:
        from pptx import Presentation

        prs = Presentation(str(path))
        slides = list(prs.slides)
        parts: list[str] = []
        total = 0
        for idx, slide in enumerate(slides[: max(pages, 1) * SLIDES_PER_PAGE], start=1):
            texts = _shape_texts(slide.shapes)
            title_shape = slide.shapes.title
            title = str(title_shape.text_frame.text).strip() if title_shape is not None else ""
            if title:
                texts = [title, *[t for t in texts if t != title]]
            if texts:
                chunk = f"Slide {idx}: " + "\n".join(texts)
                parts.append(chunk)
                total += len(chunk) + 1
            if total >= max_chars:
                break
        meta: dict[str, Any] = {}
        cp = prs.core_properties
        if cp.title:
            meta["title"] = cp.title.strip()
        if cp.author:
            meta["author"] = cp.author.strip()
        if cp.created:
            meta["created"] = cp.created.replace(microsecond=0).isoformat()
        return ExtractResult(
            mime=PPTX, text="\n".join(parts)[:max_chars], metadata=meta, page_count=len(slides)
        )
