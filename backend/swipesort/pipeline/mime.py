"""Content-based MIME sniffing. Pure Python (no libmagic, which is unavailable on Windows)."""

from __future__ import annotations

import mimetypes
import zipfile
from pathlib import Path

DIRECTORY_MIME = "inode/directory"
OCTET = "application/octet-stream"

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Built-in extension table, consulted before `mimetypes`: the stdlib reads the Windows registry,
# so its answers differ across machines. This keeps sniffing identical on macOS and Windows.
EXT_MIME: dict[str, str] = {
    "pdf": PDF,
    "docx": DOCX,
    "pptx": PPTX,
    "xlsx": XLSX,
    "doc": "application/msword",
    "ppt": "application/vnd.ms-powerpoint",
    "xls": "application/vnd.ms-excel",
    "rtf": "application/rtf",
    "odt": "application/vnd.oasis.opendocument.text",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "heic": "image/heic",
    "heif": "image/heif",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
    "svg": "image/svg+xml",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "mkv": "video/x-matroska",
    "zip": "application/zip",
    "txt": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
    "json": "application/json",
    "html": "text/html",
    "htm": "text/html",
}

# extension (lowercase, no dot) -> language name. Sniffed text files with these extensions get
# the mime "text/x-<language>" so the code extractor can claim them.
CODE_LANGS: dict[str, str] = {
    "py": "python",
    "pyi": "python",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "java": "java",
    "kt": "kotlin",
    "swift": "swift",
    "c": "c",
    "h": "c",
    "cc": "cpp",
    "cpp": "cpp",
    "hpp": "cpp",
    "cs": "csharp",
    "go": "go",
    "rs": "rust",
    "rb": "ruby",
    "php": "php",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "ps1": "powershell",
    "bat": "batch",
    "sql": "sql",
    "r": "r",
    "lua": "lua",
    "pl": "perl",
    "scala": "scala",
    "dart": "dart",
    "vue": "vue",
    "svelte": "svelte",
    "css": "css",
    "scss": "scss",
    "html": "html",
    "htm": "html",
    "ipynb": "jupyter",
}

_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")
_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
_MP4_AUDIO_BRANDS = {b"M4A ", b"M4B ", b"M4P "}


def code_language(ext: str) -> str | None:
    return CODE_LANGS.get(ext.lower().lstrip("."))


def _sniff_zip(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return "application/zip"
    if "[Content_Types].xml" in names:
        if any(n.startswith("word/") for n in names):
            return DOCX
        if any(n.startswith("ppt/") for n in names):
            return PPTX
        if any(n.startswith("xl/") for n in names):
            return XLSX
    if "mimetype" in names:
        try:
            with zipfile.ZipFile(path) as zf:
                declared = zf.read("mimetype")[:100].decode("ascii", "ignore").strip()
            if declared.startswith("application/"):
                return declared
        except (zipfile.BadZipFile, OSError, KeyError):
            pass
    return "application/zip"


def _sniff_magic(head: bytes, path: Path) -> str | None:
    if head.startswith(b"%PDF"):
        return PDF
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _HEIC_BRANDS:
            return "image/heic"
        if brand == b"qt  ":
            return "video/quicktime"
        if brand in _MP4_AUDIO_BRANDS:
            return "audio/mp4"
        return "video/mp4"
    if head.startswith(_ZIP_MAGIC):
        return _sniff_zip(path)
    if head.startswith(b"ID3"):
        return "audio/mpeg"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0 and (head[1] & 0x06) != 0:
        return "audio/mpeg"  # MPEG audio frame sync
    return None


def _decodes_as_text(head: bytes) -> bool:
    if not head or b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        # a multi-byte char cut off at the 4KB boundary is fine
        if e.start < len(head) - 3 and e.reason != "unexpected end of data":
            return False
        try:
            head[: e.start].decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True


def sniff_mime(path: Path) -> str:
    """Best-effort MIME type from content, falling back to extension, then text heuristic."""
    path = Path(path)
    try:
        if path.is_dir():
            return DIRECTORY_MIME
        with path.open("rb") as f:
            head = f.read(4096)
    except OSError:
        return OCTET
    magic = _sniff_magic(head, path)
    if magic:
        return magic
    ext = path.suffix.lower().lstrip(".")
    is_text = _decodes_as_text(head)
    lang = CODE_LANGS.get(ext)
    if lang and (is_text or not head):
        return f"text/x-{lang}"
    guessed = EXT_MIME.get(ext) or mimetypes.guess_type(path.name, strict=False)[0]
    if guessed:
        return guessed
    if is_text:
        return "text/plain"
    return OCTET
