"""macOS platform: Spotlight metadata, Quick Look thumbnails, osascript notifications."""

from __future__ import annotations

import logging
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from swipesort.platform.base import SourceMetadata
from swipesort.platform.fallback import FallbackPlatform

log = logging.getLogger(__name__)

_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_SUBPROCESS_TIMEOUT = 10.0


def _run(args: list[str], timeout: float = _SUBPROCESS_TIMEOUT) -> str | None:
    """Run a command, returning stdout or None on any failure."""
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def parse_mdls_list(raw: str) -> list[str]:
    """Parse the plist-ish array that `mdls -raw` prints. '(null)' / junk -> []."""
    text = raw.strip()
    if not text or text == "(null)":
        return []
    items: list[str] = []
    for line in text.splitlines():
        line = line.strip().rstrip(",").strip()
        if not line or line in ("(", ")"):
            continue
        m = _QUOTED.fullmatch(line)
        items.append(m.group(1).replace('\\"', '"').replace("\\\\", "\\") if m else line)
    return items


def _mdls(path: Path, attr: str) -> list[str]:
    out = _run(["mdls", "-raw", "-name", attr, str(path)])
    return parse_mdls_list(out) if out else []


class MacOSPlatform(FallbackPlatform):
    name = "macos"

    def source_metadata(self, path: Path) -> SourceMetadata:
        return SourceMetadata(where_froms=_mdls(path, "kMDItemWhereFroms"))

    def user_tags(self, path: Path) -> list[str]:
        # Tags with a Finder colour are stored as "Name\n<colour index>".
        return [t.split("\\n")[0].split("\n")[0] for t in _mdls(path, "kMDItemUserTags")]

    def thumbnail(self, path: Path, out: Path, size: int = 256) -> Path | None:
        try:
            with tempfile.TemporaryDirectory(prefix="swipesort-ql-") as tmp:
                produced = _run(["qlmanage", "-t", "-s", str(size), "-o", tmp, str(path)], 30.0)
                candidate = Path(tmp) / f"{path.name}.png"
                if produced is not None and candidate.is_file():
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(candidate), out)
                    return out
        except OSError:
            log.debug("qlmanage thumbnail failed for %s", path, exc_info=True)
        return super().thumbnail(path, out, size)

    def is_cloud_placeholder(self, path: Path) -> bool:
        return path.name.startswith(".") and path.name.endswith(".icloud")

    def is_hidden(self, path: Path) -> bool:
        if path.name.startswith("."):
            return True
        try:
            flags = getattr(path.lstat(), "st_flags", 0)
        except OSError:
            return False
        return bool(flags & stat.UF_HIDDEN)

    def _access_guidance(self, root: Path) -> str:
        return (
            f"macOS blocked access to {root}. Open System Settings > Privacy & Security > "
            "Full Disk Access (or Files & Folders), enable your terminal / SwipeSort, then retry."
        )

    def notify(self, title: str, body: str, undo_url: str | None = None) -> None:
        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')

        script = f'display notification "{esc(body)}" with title "{esc(title)}"'
        if _run(["osascript", "-e", script]) is None:
            super().notify(title, body, undo_url)
