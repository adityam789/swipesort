"""Fallback platform: pure-Python behaviour that works everywhere."""

from __future__ import annotations

import logging
from pathlib import Path

from swipesort.platform.base import AccessReport, SourceMetadata

log = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


class FallbackPlatform:
    """Baseline implementation; OS-specific platforms subclass it."""

    name: str = "fallback"

    def ocr(self, image: Path) -> str:
        return ""  # rapidocr arrives in M1

    def source_metadata(self, path: Path) -> SourceMetadata:
        return SourceMetadata()

    def user_tags(self, path: Path) -> list[str]:
        return []

    def thumbnail(self, path: Path, out: Path, size: int = 256) -> Path | None:
        if path.suffix.lower() not in _IMAGE_SUFFIXES:
            return None
        try:
            from PIL import Image
        except ImportError:
            return None
        try:
            with Image.open(path) as img:
                img.thumbnail((size, size))
                converted = img if img.mode in ("RGB", "RGBA", "L") else img.convert("RGBA")
                out.parent.mkdir(parents=True, exist_ok=True)
                converted.save(out, format="PNG")
        except Exception:  # corrupt / unsupported image: no thumbnail
            log.debug("thumbnail failed for %s", path, exc_info=True)
            return None
        return out

    def send_to_os_trash(self, path: Path) -> bool:
        try:
            from send2trash import send2trash
        except ImportError:
            return False
        try:
            send2trash(str(path))
        except Exception:
            log.warning("send2trash failed for %s", path, exc_info=True)
            return False
        return True

    def is_cloud_placeholder(self, path: Path) -> bool:
        return False

    def is_hidden(self, path: Path) -> bool:
        return path.name.startswith(".")

    def _access_guidance(self, root: Path) -> str:
        return f"Cannot read {root}. Check that the folder exists and you have permission."

    def check_access(self, root: Path) -> AccessReport:
        try:
            next(iter(root.iterdir()), None)
        except PermissionError:
            return AccessReport(ok=False, message=self._access_guidance(root))
        except OSError as exc:
            return AccessReport(ok=False, message=f"Cannot read {root}: {exc}")
        return AccessReport(ok=True)

    def notify(self, title: str, body: str, undo_url: str | None = None) -> None:
        log.info("notify: %s - %s%s", title, body, f" (undo: {undo_url})" if undo_url else "")

    def install_background_agent(self) -> bool:
        return False  # manual `swipesort serve --watch`

    def uninstall_background_agent(self) -> bool:
        return False

    def background_agent_status(self) -> str:
        return "unsupported"

    def long_path(self, path: Path) -> Path:
        return path

    def same_volume(self, a: Path, b: Path) -> bool:
        return a.stat().st_dev == b.stat().st_dev

    def is_locked(self, path: Path) -> bool:
        return False
