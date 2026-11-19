"""Watch mode units (SPEC §18): settle logic (fake clock), ignore patterns, safety gating,
event -> unit mapping, background-agent definitions, notification commands, targeted undo."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest
from sqlmodel import Session as DBSession

from swipesort.config import Scan, Settings, Watch, WatchFolder, load_settings
from swipesort.db.models import Decision
from swipesort.db.models import Session as SessionRow
from swipesort.db.session import make_engine
from swipesort.exec.errors import UndoConflictError
from swipesort.exec.executor import Executor
from swipesort.pipeline.scanner import scan_entry
from swipesort.platform import agent
from swipesort.platform.fallback import FallbackPlatform
from swipesort.platform.macos import notify_command
from swipesort.platform.windows import toast_script
from swipesort.watch import policy
from swipesort.watch.settle import Settler, is_ignored, signature
from swipesort.watch.watcher import unit_for


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# ---------------------------------------------------------------- settle
def test_settle_waits_for_stable_size_and_mtime(tmp_path: Path) -> None:
    clock = Clock()
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    s = Settler(5.0, lambda p: False, clock)
    s.touch(f)
    assert s.poll() == []
    clock.t += 4.9
    assert s.poll() == []
    f.write_bytes(b"xx")  # still growing: the clock restarts at this poll
    clock.t += 0.2
    assert s.poll() == []
    clock.t += 4.9
    assert s.poll() == []
    clock.t += 0.2
    assert s.poll() == [f]
    assert f not in s and len(s) == 0


def test_settle_event_restarts_clock(tmp_path: Path) -> None:
    clock = Clock()
    f = tmp_path / "a.txt"
    f.write_text("x")
    s = Settler(1.0, lambda p: False, clock)
    s.touch(f)
    clock.t += 0.9
    s.touch(f)  # a modified event, same size
    clock.t += 0.5
    assert s.poll() == []
    clock.t += 0.6
    assert s.poll() == [f]


def test_settle_waits_while_locked(tmp_path: Path) -> None:
    clock = Clock()
    f = tmp_path / "a.zip"
    f.write_bytes(b"data")
    locked = {f}
    s = Settler(1.0, lambda p: p in locked, clock)
    s.touch(f)
    clock.t += 10
    assert s.poll() == []  # stable but the writer still holds it
    locked.clear()
    assert s.poll() == [f]


def test_settle_drops_vanished_paths(tmp_path: Path) -> None:
    clock = Clock()
    f = tmp_path / "a.pdf.crdownload"
    f.write_bytes(b"x")
    s = Settler(1.0, lambda p: False, clock)
    s.touch(f)
    f.unlink()  # renamed away
    clock.t += 5
    assert s.poll() == [] and len(s) == 0


def test_settle_folder_signature(tmp_path: Path) -> None:
    d = tmp_path / "proj"
    d.mkdir()
    (d / "a.txt").write_text("1")
    s1 = signature(d)
    (d / "b.txt").write_text("22")
    s2 = signature(d)
    assert s1 is not None and s2 is not None and s1 != s2 and s2[0] == 2
    assert signature(tmp_path / "missing") is None


def test_ignore_patterns() -> None:
    pats = Watch().ignore
    for name in ("report.pdf.crdownload", "x.PART", "f.download", "a.tmp", "b.partial",
                 "~$budget.xlsx"):
        assert is_ignored([name], pats), name
    assert is_ignored(["paper.pdf.download", "data"], pats)  # Safari's bundle folder
    assert not is_ignored(["report.pdf"], pats)
    assert not is_ignored(["partial-derivatives.pdf"], pats)


# ---------------------------------------------------------------- config
def test_watch_folders_accept_plain_strings() -> None:
    w = Watch.model_validate({"folders": ["~/Downloads", {"path": "/a", "output": "/b"}],
                              "default_output": "/sorted"})
    assert [f.path for f in w.folders] == ["~/Downloads", "/a"]
    res = w.resolved_folders()
    assert [(f.path, f.output) for f in res] == [("~/Downloads", "/sorted"), ("/a", "/b")]
    assert Watch().resolved_folders() == []  # no output configured: nothing to watch
    assert Watch().min_decisions_for_auto == 50 and Watch().auto_move_threshold == 0.97
    assert Watch().auto_trash_exact_dupes is False


def test_watch_overlay_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "watch.json").write_text(
        '{"enabled": true, "folders": [{"path": "/in", "output": "/out"}]}')
    s = load_settings(tmp_path / "config.toml")
    assert s.watch.enabled and s.watch.folders == [WatchFolder(path="/in", output="/out")]
    (tmp_path / "watch.json").write_text("not json")
    monkeypatch.setenv("SWIPESORT_WATCH", "1")
    s = load_settings(tmp_path / "config.toml")
    assert s.watch.enabled and s.watch.folders[0].path == "~/Downloads"


# ---------------------------------------------------------------- policy / safety
def _d(cfg: Watch, **kw: object) -> str:
    base: dict[str, object] = {"decisions": 100, "tier": "confirm", "confidence": 0.99,
                               "dup_relation": None, "dup_in_output": False}
    base.update(kw)
    return policy.decide(cfg, **base).action  # type: ignore[arg-type]


def test_policy_threshold_and_tier() -> None:
    cfg = Watch()
    assert _d(cfg) == "move"
    assert _d(cfg, confidence=0.969) == "pending"
    assert _d(cfg, tier="choices", confidence=0.99) == "pending"
    assert _d(cfg, tier=None, confidence=0.0) == "pending"


def test_policy_safety_gate() -> None:
    cfg = Watch()
    assert _d(cfg, decisions=49) == "pending"
    assert _d(cfg, decisions=50) == "move"
    assert not policy.auto_unlocked(cfg, 12) and policy.auto_unlocked(cfg, 50)
    off = Watch(auto_move=False)
    assert _d(off) == "pending"


def test_policy_duplicates() -> None:
    cfg = Watch()
    assert _d(cfg, dup_relation="exact", dup_in_output=True) == "pending"  # default off
    on = Watch(auto_trash_exact_dupes=True)
    assert _d(on, dup_relation="exact", dup_in_output=True) == "trash"
    assert _d(on, dup_relation="exact", dup_in_output=True, decisions=3) == "pending"
    assert _d(on, dup_relation="exact", dup_in_output=False) == "pending"  # input-input
    assert _d(on, dup_relation="near_image", dup_in_output=True) == "pending"


# ---------------------------------------------------------------- event -> unit
def test_unit_for(tmp_path: Path) -> None:
    root = tmp_path / "Downloads"
    (root / "proj" / "src").mkdir(parents=True)
    (root / "proj" / "src" / "x.py").write_text("x")
    (root / "a.pdf").write_text("x")
    (root / ".hidden").write_text("x")
    out = root / "Sorted"
    out.mkdir()
    fb = FallbackPlatform()
    sc, ig = Scan(), Watch().ignore
    assert unit_for(root / "a.pdf", root, sc, fb, ig) == root / "a.pdf"
    assert unit_for(root / "proj" / "src" / "x.py", root, sc, fb, ig) == root / "proj"
    assert unit_for(root / "a.pdf.crdownload", root, sc, fb, ig) is None
    assert unit_for(root / ".hidden", root, sc, fb, ig) is None
    assert unit_for(root / ".swipesort" / "trash" / "x", root, sc, fb, ig) is None
    assert unit_for(root, root, sc, fb, ig) is None
    assert unit_for(tmp_path / "elsewhere.pdf", root, sc, fb, ig) is None
    assert unit_for(out / "Finance" / "a.pdf", root, sc, fb, ig, [out]) is None


def test_scan_entry_bundle_and_plain_folder(tmp_path: Path) -> None:
    root = tmp_path / "Downloads"
    app = root / "Tool.app" / "Contents"
    app.mkdir(parents=True)
    (app / "Info.plist").write_text("x")
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "main.py").write_text("x")
    plain = root / "photos"
    plain.mkdir()
    (plain / "a.jpg").write_bytes(b"1")
    (plain / "b.jpg").write_bytes(b"2")
    fb, sc = FallbackPlatform(), Scan()
    items = scan_entry(root / "Tool.app", root, sc, fb)
    assert [(i.rel_path, i.is_bundle) for i in items] == [("Tool.app", True)]
    items = scan_entry(repo, root, sc, fb)
    assert [(i.rel_path, i.is_bundle) for i in items] == [("repo", True)]
    items = scan_entry(plain, root, sc, fb)
    assert [i.rel_path for i in items] == ["photos/a.jpg", "photos/b.jpg"]


# ---------------------------------------------------------------- background agent
def test_launchd_plist() -> None:
    raw = agent.launchd_plist("/opt/py/bin/python3", Path("/Users/u/.swipesort/logs"),
                              Path("/Users/u/.swipesort"))
    spec = plistlib.loads(raw)
    assert spec["Label"] == "com.swipesort.daemon"
    assert spec["ProgramArguments"] == ["/opt/py/bin/python3", "-m", "swipesort", "serve",
                                        "--watch"]
    assert spec["RunAtLoad"] is True and spec["KeepAlive"] is True
    assert spec["StandardErrorPath"].endswith("daemon.err.log")
    assert agent.launchd_plist_path(Path("/Users/u")) == Path(
        "/Users/u/Library/LaunchAgents/com.swipesort.daemon.plist")
    assert agent.launchctl_bootstrap_args(501, Path("/p.plist")) == [
        "launchctl", "bootstrap", "gui/501", str(Path("/p.plist"))]
    assert agent.launchctl_bootout_args(501) == [
        "launchctl", "bootout", "gui/501/com.swipesort.daemon"]


def test_schtasks_args() -> None:
    args = agent.schtasks_create_args(r"C:\Program Files\Python312\pythonw.exe")
    assert args[:4] == ["schtasks", "/Create", "/TN", "SwipeSort"]
    tr = args[args.index("/TR") + 1]
    assert tr == r'"C:\Program Files\Python312\pythonw.exe" -m swipesort serve --watch'
    assert args[args.index("/SC") + 1] == "ONLOGON" and "/F" in args
    assert agent.schtasks_delete_args() == ["schtasks", "/Delete", "/TN", "SwipeSort", "/F"]


def test_agent_never_installed_by_fallback() -> None:
    fb = FallbackPlatform()
    assert fb.install_background_agent() is False
    assert fb.uninstall_background_agent() is False
    assert fb.background_agent_status() == "unsupported"


# ---------------------------------------------------------------- notifications
def test_macos_notify_command() -> None:
    cmd = notify_command("SwipeSort", 'Sorted "a".pdf → Finance', None, None)
    assert cmd[:2] == ["osascript", "-e"]
    assert cmd[2] == ('display notification "Sorted \\"a\\".pdf → Finance" '
                      'with title "SwipeSort"')
    tn = notify_command("SwipeSort", "b", "http://127.0.0.1:8765/watch?undo=3",
                        "/opt/homebrew/bin/terminal-notifier")
    assert tn[0].endswith("terminal-notifier")
    assert tn[tn.index("-open") + 1] == "http://127.0.0.1:8765/watch?undo=3"


def test_windows_toast_script() -> None:
    ps = toast_script("SwipeSort", "Sorted it's <x> & y", "http://127.0.0.1:8765/watch?undo=3&a=1")
    assert "ToastNotificationManager" in ps
    assert "it''s &lt;x&gt; &amp; y" in ps  # XML-escaped, PowerShell-quoted
    assert 'launch="http://127.0.0.1:8765/watch?undo=3&amp;a=1"' in ps
    assert 'activationType="protocol"' in ps
    assert "activationType" not in toast_script("t", "b", None)


# ---------------------------------------------------------------- targeted undo
def _env(tmp_path: Path) -> tuple[Executor, Path, Path]:
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    eng = make_engine(":memory:")
    with DBSession(eng) as db:
        db.add(SessionRow(id=1, input_root=str(inp), output_root=str(out)))
        db.commit()
        for i in range(1, 6):
            db.add(Decision(id=i, session_id=1, action="pick"))
        db.commit()
    return Executor(eng, 1, inp, out, platform=FallbackPlatform(), settings=Settings()), inp, out


def test_undo_decision_out_of_order(tmp_path: Path) -> None:
    ex, inp, out = _env(tmp_path)
    (inp / "a.txt").write_text("a")
    (inp / "b.txt").write_text("b")
    ex.move(inp / "a.txt", out / "A", decision_id=1)
    ex.move(inp / "b.txt", out / "B", decision_id=2)
    assert ex.undo_conflict(1) is None
    ex.undo_decision(1)  # the older one, while 2 stays
    assert (inp / "a.txt").exists() and not (out / "A").exists()
    assert (out / "B" / "b.txt").exists()
    assert ex.undo_conflict(1) == "nothing to undo"


def test_undo_decision_refuses_dependent_ops(tmp_path: Path) -> None:
    ex, inp, out = _env(tmp_path)
    (inp / "a.txt").write_text("a")
    (inp / "c.txt").write_text("c")
    ex.move(inp / "a.txt", out / "A", decision_id=1)  # creates A/ (mkdir op)
    ex.move(out / "A" / "a.txt", out / "Z", decision_id=2)  # moved again
    with pytest.raises(UndoConflictError, match="moved again"):
        ex.undo_decision(1)
    ex.undo_decision(2)
    ex.move(inp / "c.txt", out / "A", decision_id=3)  # something else lands in A/
    with pytest.raises(UndoConflictError, match="used by a later change"):
        ex.undo_decision(1)
    ex.undo_decision(3)
    ex.undo_decision(1)
    assert (inp / "a.txt").exists() and (inp / "c.txt").exists()
