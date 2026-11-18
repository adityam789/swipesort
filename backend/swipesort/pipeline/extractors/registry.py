"""Extractor registry. `extract` is a top-level function so it pickles into a process pool."""

from __future__ import annotations

from pathlib import Path

from swipesort.pipeline.extractors.base import Extractor, ExtractResult
from swipesort.pipeline.extractors.code import BundleExtractor, CodeExtractor
from swipesort.pipeline.extractors.docx import DocxExtractor
from swipesort.pipeline.extractors.image import ImageExtractor
from swipesort.pipeline.extractors.media import MediaExtractor
from swipesort.pipeline.extractors.pdf import PdfExtractor
from swipesort.pipeline.extractors.pptx import PptxExtractor
from swipesort.pipeline.extractors.text import TextExtractor
from swipesort.pipeline.extractors.xlsx import XlsxExtractor
from swipesort.pipeline.mime import OCTET, sniff_mime

# Order matters: code before text (text claims every text/*).
EXTRACTORS: tuple[Extractor, ...] = (
    PdfExtractor(),
    DocxExtractor(),
    PptxExtractor(),
    XlsxExtractor(),
    ImageExtractor(),
    MediaExtractor(),
    CodeExtractor(),
    BundleExtractor(),
    TextExtractor(),
)


def pick(mime: str) -> Extractor | None:
    for ex in EXTRACTORS:
        if ex.supports(mime):
            return ex
    return None


def extract(path: Path, max_chars: int, pages: int) -> ExtractResult:
    """Extract text + metadata from `path`. Never raises."""
    mime = OCTET
    try:
        path = Path(path)
        mime = sniff_mime(path)
        ex = pick(mime)
        if ex is None:
            return ExtractResult(mime=mime)
        result = ex.extract(path, max_chars, pages)
        if result.mime != mime:
            result = result.model_copy(update={"mime": mime})
        return result
    except Exception as e:  # noqa: BLE001 - contract: never raise (corrupt files, etc.)
        return ExtractResult(mime=mime, metadata={"error": f"{type(e).__name__}: {e}"[:300]})
