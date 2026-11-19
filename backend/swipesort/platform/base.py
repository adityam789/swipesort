"""Platform protocol (SPEC §10). Only swipesort/platform/* may inspect sys.platform."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class SourceMetadata(BaseModel):
    where_froms: list[str] = []  # download URLs (macOS kMDItemWhereFroms / Windows Zone.Identifier)
    authors: list[str] = []
    content_type: str = ""


class AccessReport(BaseModel):
    ok: bool
    message: str = ""  # human guidance if not ok (Full Disk Access / Controlled Folder Access)


@runtime_checkable
class Platform(Protocol):
    name: str  # "macos" | "windows" | "fallback"

    def ocr(self, image: Path) -> str: ...
    def source_metadata(self, path: Path) -> SourceMetadata: ...
    def user_tags(self, path: Path) -> list[str]: ...
    def thumbnail(self, path: Path, out: Path, size: int = 256) -> Path | None: ...
    def send_to_os_trash(self, path: Path) -> bool: ...
    def is_cloud_placeholder(self, path: Path) -> bool: ...
    def is_hidden(self, path: Path) -> bool: ...  # dotfiles + OS hidden/system attrs
    def check_access(self, root: Path) -> AccessReport: ...
    def notify(self, title: str, body: str, undo_url: str | None = None) -> None: ...
    def install_background_agent(self) -> bool: ...  # login agent running `serve --watch`
    def uninstall_background_agent(self) -> bool: ...
    def background_agent_status(self) -> str: ...  # "installed" | "not installed" | "unsupported"
    def long_path(self, path: Path) -> Path: ...  # Windows: \\?\ prefix when > 260 chars; else identity
    def same_volume(self, a: Path, b: Path) -> bool: ...
    def is_locked(self, path: Path) -> bool: ...  # open exclusively by another process (best effort)
