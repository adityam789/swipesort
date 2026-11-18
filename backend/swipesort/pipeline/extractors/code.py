"""Source-code files and code-project bundles (directories)."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from swipesort.pipeline.extractors.base import ExtractResult
from swipesort.pipeline.extractors.text import read_head
from swipesort.pipeline.mime import CODE_LANGS, DIRECTORY_MIME, code_language

_CODE_MIMES = {f"text/x-{lang}" for lang in CODE_LANGS.values()}

# marker (exact name or suffix) -> bundle kind; first match wins
_BUNDLE_MARKERS: tuple[tuple[str, str], ...] = (
    (".git", "git"),
    ("package.json", "node"),
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("requirements.txt", "python"),
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("Package.swift", "swift"),
    (".xcodeproj", "xcode"),
    (".xcworkspace", "xcode"),
    (".sln", "dotnet"),
    (".app", "app"),
)
_README_CHARS = 800
_MAX_ENTRIES = 40


class CodeExtractor:
    def supports(self, mime: str) -> bool:
        return mime in _CODE_MIMES

    def extract(self, path: Path, max_chars: int, pages: int) -> ExtractResult:
        lang = code_language(path.suffix) or "code"
        text = read_head(path, max_chars)
        return ExtractResult(
            mime=f"text/x-{lang}",
            text=text,
            metadata={"language": lang, "line_count_head": text.count("\n") + 1 if text else 0},
        )


def _bundle_kind(names: list[str], dirname: str) -> str:
    if dirname.lower().endswith(".app"):
        return "app"
    for marker, kind in _BUNDLE_MARKERS:
        if marker.startswith("."):
            if any(n == marker or n.lower().endswith(marker) for n in names):
                return kind
        elif marker in names:
            return kind
    return "folder"


def _safe_read(path: Path, limit: int) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        return read_head(path, limit)
    except OSError:
        return ""


def _project_info(root: Path, names: list[str]) -> tuple[str, str]:
    """(name, description) from package.json / pyproject.toml / Cargo.toml if present."""
    if "package.json" in names:
        try:
            data: Any = json.loads(_safe_read(root / "package.json", 20000))
            if isinstance(data, dict):
                return str(data.get("name", "")), str(data.get("description", ""))
        except ValueError:
            pass
    for fname in ("pyproject.toml", "Cargo.toml"):
        if fname in names:
            try:
                toml: Any = tomllib.loads(_safe_read(root / fname, 20000))
            except (tomllib.TOMLDecodeError, ValueError):
                continue
            proj = toml.get("project") or toml.get("package") or toml.get("tool", {}).get("poetry")
            if isinstance(proj, dict):
                return str(proj.get("name", "")), str(proj.get("description", ""))
    return "", ""


class BundleExtractor:
    """Directories treated as one unit (git repos, node/python projects, .app, ...)."""

    def supports(self, mime: str) -> bool:
        return mime == DIRECTORY_MIME

    def extract(self, path: Path, max_chars: int, pages: int) -> ExtractResult:
        with os.scandir(path) as it:
            entries = sorted(it, key=lambda e: e.name.lower())
        names = [e.name for e in entries]
        kind = _bundle_kind(names, path.name)
        meta: dict[str, Any] = {"bundle_kind": kind, "entry_count": len(names)}
        parts: list[str] = []
        name, desc = _project_info(path, names)
        if name:
            meta["title"] = name
            parts.append(f"Project: {name}")
        if desc:
            parts.append(f"Description: {desc}")
        readme = next((e for e in entries if e.name.lower().startswith("readme")), None)
        if readme is not None:
            head = _safe_read(Path(readme.path), _README_CHARS).strip()
            if head:
                parts.append(f"README: {head}")
        shown = [n + ("/" if e.is_dir(follow_symlinks=False) else "") for n, e in zip(
            names, entries, strict=True)][:_MAX_ENTRIES]
        parts.append("Contents: " + ", ".join(shown))
        return ExtractResult(
            mime=DIRECTORY_MIME, text="\n".join(parts)[:max_chars], metadata=meta
        )
