"""Windows platform: file attributes, Zone.Identifier ADS, long paths, lock detection."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from swipesort.platform.base import SourceMetadata
from swipesort.platform.fallback import FallbackPlatform

log = logging.getLogger(__name__)

FILE_ATTRIBUTE_HIDDEN: int = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
FILE_ATTRIBUTE_SYSTEM: int = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
FILE_ATTRIBUTE_OFFLINE: int = getattr(stat, "FILE_ATTRIBUTE_OFFLINE", 0x1000)
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000

_CLOUD_MASK = FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS | FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN
_HIDDEN_MASK = FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
_JUNK_NAMES = {"desktop.ini", "thumbs.db"}
_LONG_PATH_LIMIT = 260


def _attrs(path: Path) -> int:
    try:
        return int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError:
        return 0


def parse_zone_identifier(text: str) -> SourceMetadata:
    """Parse the contents of a Zone.Identifier stream into SourceMetadata."""
    urls: list[str] = []
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() in ("HostUrl", "ReferrerUrl") and value.strip():
            urls.append(value.strip())
    return SourceMetadata(where_froms=urls)


class WindowsPlatform(FallbackPlatform):
    name = "windows"

    def is_hidden(self, path: Path) -> bool:
        if path.name.startswith(".") or path.name.lower() in _JUNK_NAMES:
            return True
        return bool(_attrs(path) & _HIDDEN_MASK)

    def is_cloud_placeholder(self, path: Path) -> bool:
        return bool(_attrs(path) & _CLOUD_MASK)

    def source_metadata(self, path: Path) -> SourceMetadata:
        try:
            with open(f"{path}:Zone.Identifier", encoding="utf-8", errors="replace") as fh:
                return parse_zone_identifier(fh.read())
        except OSError:
            return SourceMetadata()

    def long_path(self, path: Path) -> Path:
        text = str(path)
        if text.startswith("\\\\?\\") or not path.is_absolute() or len(text) <= _LONG_PATH_LIMIT:
            return path
        if text.startswith("\\\\"):  # UNC: \\server\share\... -> \\?\UNC\server\share\...
            return Path("\\\\?\\UNC\\" + text[2:])
        return Path("\\\\?\\" + text)

    def is_locked(self, path: Path) -> bool:
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(self.long_path(path), flags)
        except PermissionError:
            return True
        except OSError:
            return False
        os.close(fd)
        return False

    def _access_guidance(self, root: Path) -> str:
        return (
            f"Windows blocked access to {root}. If Controlled Folder Access is on, open Windows "
            "Security > Virus & threat protection > Ransomware protection > Allow an app through "
            "Controlled folder access, add SwipeSort / your terminal, then retry."
        )

    def notify(self, title: str, body: str, undo_url: str | None = None) -> None:
        try:
            from windows_toasts import Toast, WindowsToaster  # type: ignore[import-not-found,unused-ignore]
        except ImportError:
            super().notify(title, body, undo_url)
            return
        try:
            toaster = WindowsToaster("SwipeSort")
            toast = Toast([title, body])
            toaster.show_toast(toast)
        except Exception:
            log.debug("toast failed", exc_info=True)
            super().notify(title, body, undo_url)
