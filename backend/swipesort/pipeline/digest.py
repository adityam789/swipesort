"""Digest builder (SPEC §3): ~500 tokens of deterministic text describing one file."""

from __future__ import annotations

import re
from typing import Any

from swipesort.pipeline.extractors.base import ExtractResult
from swipesort.pipeline.mime import DIRECTORY_MIME, DOCX, PDF, PPTX, XLSX

_SEP = re.compile(r"[\W_]+", re.UNICODE)
_RUN = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)
_EXT_SUFFIX = re.compile(r"\.([A-Za-z0-9]{1,5})$")
_WS = re.compile(r"\s+")
_SCREENSHOT_NAME = re.compile(
    r"screen\s*shot|screen\s*capture|screen\s*recording|snip|capture", re.IGNORECASE
)

_KIND_WORDS: dict[str, str] = {
    PDF: "pdf document",
    DOCX: "word document",
    PPTX: "powerpoint presentation slides",
    XLSX: "excel spreadsheet table",
    "text/plain": "text note",
    "text/markdown": "markdown note text",
    "text/csv": "csv table data",
    DIRECTORY_MIME: "folder bundle project",
}

# metadata key -> label, in output order
_META_KEYS: tuple[tuple[str, str], ...] = (
    ("title", "title"),
    ("author", "author"),
    ("subject", "subject"),
    ("created", "created"),
    ("exif_date", "taken"),
    ("camera", "camera"),
    ("artist", "artist"),
    ("album", "album"),
    ("genre", "genre"),
    ("date", "date"),
    ("language", "language"),
    ("bundle_kind", "project type"),
    ("sheets", "sheets"),
)


def _split_camel(chunk: str) -> list[str]:
    out: list[str] = []
    cur = ""
    for i, c in enumerate(chunk):
        if cur:
            prev = chunk[i - 1]
            nxt = chunk[i + 1] if i + 1 < len(chunk) else ""
            if c.isupper() and (prev.islower() or (prev.isupper() and nxt.islower())):
                out.append(cur)
                cur = ""
        cur += c
    if cur:
        out.append(cur)
    return out


def tokenize_name(name: str) -> list[str]:
    """Lowercase tokens from a file name: drops extension; splits snake/kebab/camelCase/digits.

    Short letter prefixes glued to digits stay together ("Q3" -> "q3", "finalV2" -> final, v2).
    """
    m = _EXT_SUFFIX.search(name)
    if m and re.search(r"[A-Za-z]", m.group(1)) and m.start() > 0:
        name = name[: m.start()]
    tokens: list[str] = []
    for chunk in _SEP.split(name):
        if not chunk:
            continue
        pieces: list[str] = []
        for run in _RUN.findall(chunk):
            if run.isdigit():
                pieces.append(run)
            else:
                pieces.extend(_split_camel(run))
        merged: list[str] = []
        i = 0
        while i < len(pieces):
            p = pieces[i]
            if (
                not p.isdigit()
                and len(p) <= 2
                and i + 1 < len(pieces)
                and pieces[i + 1].isdigit()
            ):
                merged.append(p + pieces[i + 1])
                i += 2
                continue
            merged.append(p)
            i += 1
        tokens.extend(t.lower() for t in merged)
    return tokens


def _path_tokens(rel_path: str) -> list[str]:
    parts = [p for p in rel_path.replace("\\", "/").split("/")[:-1] if p]
    out: list[str] = []
    for p in parts:
        out.extend(tokenize_name(p + "."))  # trailing dot: never strip a "dir.ext" suffix
    return out


def _is_screenshot(name: str, mime: str, meta: dict[str, Any]) -> bool:
    if mime not in ("image/png", "image/jpeg") or meta.get("camera") or meta.get("exif_date"):
        return False
    if _SCREENSHOT_NAME.search(name):
        return True
    w, h = meta.get("width"), meta.get("height")
    if mime == "image/png" and isinstance(w, int) and isinstance(h, int) and h > 0:
        long_, short = max(w, h), min(w, h)
        ratio = long_ / short
        return short >= 480 and any(abs(ratio - r) < 0.03 for r in (16 / 9, 16 / 10, 19.5 / 9))
    return False


def _kind_words(name: str, ext: str, mime: str, meta: dict[str, Any]) -> str:
    if mime in _KIND_WORDS:
        words = _KIND_WORDS[mime]
        if mime == DIRECTORY_MIME and meta.get("bundle_kind") in ("git", "node", "python"):
            words += " code repository"
        return words
    if mime.startswith("image/"):
        words = "image screenshot" if _is_screenshot(name, mime, meta) else "image photo"
        return words
    if mime.startswith("audio/"):
        return "audio music recording"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("text/x-"):
        return f"source code {mime[7:]}"
    if mime.startswith("text/"):
        return "text document"
    if mime == "application/zip":
        return "zip archive"
    return "file"


def _meta_lines(meta: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for key, label in _META_KEYS:
        v = meta.get(key)
        if not v:
            continue
        if isinstance(v, list | tuple):
            v = ", ".join(str(x) for x in v)
        parts.append(f"{label}: {_WS.sub(' ', str(v)).strip()[:120]}")
    w, h = meta.get("width"), meta.get("height")
    if w and h:
        parts.append(f"size: {w}x{h}")
    if meta.get("has_gps"):
        parts.append("gps location present")
    if meta.get("duration"):
        parts.append(f"duration: {meta['duration']}s")
    return parts


def build_digest(
    name: str,
    rel_path: str,
    ext: str,
    result: ExtractResult,
    caption: str = "",
    ocr_text: str = "",
    max_chars: int = 2000,
) -> str:
    """Deterministic digest text, capped at `max_chars`."""
    meta = result.metadata
    mime = result.mime
    lines: list[str] = []
    lines.append("name: " + " ".join(tokenize_name(name)))
    ptoks = _path_tokens(rel_path)
    if ptoks:
        lines.append("folder: " + " ".join(ptoks))
    ext_l = ext.lower().lstrip(".")
    kind = _kind_words(name, ext_l, mime, meta)
    lines.append(f"kind: {kind}" + (f" (.{ext_l})" if ext_l else ""))
    if result.page_count:
        lines.append(f"pages: {result.page_count}")
    lines.extend(_meta_lines(meta))
    if caption.strip():
        lines.append("caption: " + _WS.sub(" ", caption).strip()[:400])
    if ocr_text.strip():
        lines.append("ocr: " + _WS.sub(" ", ocr_text).strip()[:600])
    head = "\n".join(lines)
    snippet = _WS.sub(" ", result.text).strip()
    if snippet:
        room = max_chars - len(head) - len("\ntext: ")
        if room > 0:
            head += "\ntext: " + snippet[:room].rstrip()
    return head[:max_chars].rstrip()
