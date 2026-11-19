"""Background agent definitions (SPEC §18): pure builders, unit-tested without installing anything.

macOS: a per-user LaunchAgent (~/Library/LaunchAgents/com.swipesort.daemon.plist) loaded with
`launchctl bootstrap gui/<uid>`. Windows: a Task Scheduler task that runs at logon.
Writing these is a configuration action, not a user-file mutation (no executor/journal).
"""

from __future__ import annotations

import plistlib
from pathlib import Path

LABEL = "com.swipesort.daemon"
TASK_NAME = "SwipeSort"


def daemon_args(python: str) -> list[str]:
    """The command the agent runs: the daemon in watch mode."""
    return [python, "-m", "swipesort", "serve", "--watch"]


def launchd_plist(python: str, log_dir: Path, workdir: Path | None = None) -> bytes:
    """LaunchAgent plist: start at login (RunAtLoad), restart if it exits (KeepAlive)."""
    spec: dict[str, object] = {
        "Label": LABEL,
        "ProgramArguments": daemon_args(python),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(log_dir / "daemon.log"),
        "StandardErrorPath": str(log_dir / "daemon.err.log"),
    }
    if workdir is not None:
        spec["WorkingDirectory"] = str(workdir)
    return plistlib.dumps(spec, fmt=plistlib.FMT_XML, sort_keys=True)


def launchd_plist_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def launchctl_bootstrap_args(uid: int, plist: Path) -> list[str]:
    return ["launchctl", "bootstrap", f"gui/{uid}", str(plist)]


def launchctl_bootout_args(uid: int) -> list[str]:
    return ["launchctl", "bootout", f"gui/{uid}/{LABEL}"]


def launchctl_print_args(uid: int) -> list[str]:
    return ["launchctl", "print", f"gui/{uid}/{LABEL}"]


def _quote_win(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or "\t" in arg) and not arg.startswith('"') else arg


def schtasks_create_args(python: str) -> list[str]:
    """Logon task for the current user (no admin rights needed with /RL LIMITED)."""
    command = " ".join(_quote_win(a) for a in daemon_args(python))
    return ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", command, "/SC", "ONLOGON",
            "/RL", "LIMITED", "/F"]


def schtasks_delete_args() -> list[str]:
    return ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]


def schtasks_query_args() -> list[str]:
    return ["schtasks", "/Query", "/TN", TASK_NAME]


def schtasks_run_args() -> list[str]:
    return ["schtasks", "/Run", "/TN", TASK_NAME]
