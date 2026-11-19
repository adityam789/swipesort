"""Windows platform: file attributes, Zone.Identifier ADS, long paths, lock detection."""

from __future__ import annotations

import ctypes
import logging
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from swipesort.platform import agent
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


_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000  # lets CreateFileW open directories too
_SHARING_VIOLATION = (32, 33)  # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
_SUBPROCESS_TIMEOUT = 15.0
# Toasts need a registered AppUserModelID; PowerShell's own one always exists.
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def toast_script(title: str, body: str, undo_url: str | None) -> str:
    """PowerShell for a basic WinRT toast (no extra modules). Clicking it opens `undo_url`
    (the app's watch activity page, where Undo lives)."""
    launch = (f' activationType="protocol" launch="{xml_escape(undo_url, {chr(34): "&quot;"})}"'
              if undo_url else "")
    xml = (f"<toast{launch}><visual><binding template=\"ToastGeneric\">"
           f"<text>{xml_escape(title)}</text><text>{xml_escape(body)}</text>"
           "</binding></visual></toast>")
    ps_xml = xml.replace("'", "''")
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType = WindowsRuntime] | Out-Null; "
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; "
        f"$x.LoadXml('{ps_xml}'); "
        "$t = [Windows.UI.Notifications.ToastNotification]::new($x); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        f"'{APP_ID}').Show($t)"
    )


def _run(args: list[str]) -> bool:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(args, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
                              check=False, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


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
        """True while another process holds the file open without sharing (e.g. a browser
        still writing a download): CreateFileW with share mode 0 fails with a sharing
        violation. Read-only files are not "locked"."""
        winDLL: Any = getattr(ctypes, "WinDLL", None)
        if winDLL is None:  # not on Windows (tests): plain open probe
            try:
                fd = os.open(self.long_path(path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
            except PermissionError:
                return True
            except OSError:
                return False
            os.close(fd)
            return False
        k32 = winDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = ctypes.c_void_p
        k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                    ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.CreateFileW(str(self.long_path(path)), _GENERIC_READ, 0, None, _OPEN_EXISTING,
                            _FILE_FLAG_BACKUP_SEMANTICS, None)
        if h is None or h == ctypes.c_void_p(-1).value:
            get_err: Any = getattr(ctypes, "get_last_error", lambda: 0)
            return int(get_err()) in _SHARING_VIOLATION
        k32.CloseHandle(h)
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
            if not _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                         toast_script(title, body, undo_url)]):
                super().notify(title, body, undo_url)
            return
        try:
            toaster = WindowsToaster("SwipeSort")
            toast = Toast([title, body])
            toaster.show_toast(toast)
        except Exception:
            log.debug("toast failed", exc_info=True)
            super().notify(title, body, undo_url)

    # ---- background agent (Task Scheduler logon task) ----
    def install_background_agent(self) -> bool:
        python = sys.executable
        pythonw = Path(python).with_name("pythonw.exe")  # no console window at logon
        if pythonw.is_file():
            python = str(pythonw)
        ok = _run(agent.schtasks_create_args(python))
        if ok:
            _run(agent.schtasks_run_args())  # start now, not only at next logon
        return ok

    def uninstall_background_agent(self) -> bool:
        return _run(agent.schtasks_delete_args())

    def background_agent_status(self) -> str:
        return "installed" if _run(agent.schtasks_query_args()) else "not installed"
