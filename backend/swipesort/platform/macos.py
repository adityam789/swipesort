"""macOS platform: Spotlight metadata, Quick Look thumbnails, osascript notifications."""

from __future__ import annotations

import importlib
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from swipesort.platform import agent
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


def _osa_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def notify_command(title: str, body: str, undo_url: str | None,
                   terminal_notifier: str | None) -> list[str]:
    """`terminal-notifier` (if installed) can open the undo page when clicked; plain
    `osascript display notification` can't carry an action, so Undo lives in the app's
    watch activity list either way."""
    if terminal_notifier:
        cmd = [terminal_notifier, "-title", title, "-message", body, "-group", "swipesort"]
        if undo_url:
            cmd += ["-open", undo_url]
        return cmd
    return ["osascript", "-e",
            f'display notification "{_osa_escape(body)}" with title "{_osa_escape(title)}"']


def _uid() -> int:
    getuid = getattr(os, "getuid", None)
    return int(getuid()) if getuid is not None else 0


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
        tn = shutil.which("terminal-notifier") if undo_url else None
        if _run(notify_command(title, body, undo_url, tn)) is None:
            super().notify(title, body, undo_url)

    def is_locked(self, path: Path) -> bool:
        """Best effort: another process holds an flock/O_EXLOCK on the file. Browsers on macOS
        don't lock downloads, so settling relies on stable size/mtime + partial-name patterns."""
        # POSIX only; imported dynamically so this module imports (and type-checks) on Windows
        fcntl: Any = importlib.import_module("fcntl")

        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except PermissionError:
            return True
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return False
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    # ---- background agent (LaunchAgent) ----
    def _plist(self) -> Path:
        return agent.launchd_plist_path(Path.home())

    def install_background_agent(self) -> bool:
        from swipesort.config import app_dir

        logs = app_dir() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        plist = self._plist()
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_bytes(agent.launchd_plist(sys.executable, logs, app_dir()))
        uid = _uid()
        _run(agent.launchctl_bootout_args(uid))  # reload if it was already loaded
        return _run(agent.launchctl_bootstrap_args(uid, plist)) is not None

    def uninstall_background_agent(self) -> bool:
        _run(agent.launchctl_bootout_args(_uid()))
        plist = self._plist()
        if plist.is_file():
            plist.unlink()  # our own config file, not user data
            return True
        return False

    def background_agent_status(self) -> str:
        if not self._plist().is_file():
            return "not installed"
        loaded = _run(agent.launchctl_print_args(_uid())) is not None
        return "installed (running)" if loaded else "installed (not loaded)"
