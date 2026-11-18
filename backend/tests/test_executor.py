from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from blake3 import blake3
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession
from sqlmodel import select

from swipesort.config import Executor as ExecutorSettings
from swipesort.config import Settings
from swipesort.db.models import Decision, Op, OpStatus, OpType
from swipesort.db.models import Session as SessionRow
from swipesort.db.session import make_engine
from swipesort.exec import journal
from swipesort.exec.errors import (
    ExecError,
    LockedFileError,
    OutsideRootsError,
    SymlinkError,
)
from swipesort.exec.executor import Executor


class FakePlatform:
    name = "fake"

    def __init__(self) -> None:
        self.same = True
        self.locked = False

    def long_path(self, path: Path) -> Path:
        return path

    def same_volume(self, a: Path, b: Path) -> bool:
        return self.same

    def is_locked(self, path: Path) -> bool:
        return self.locked

    def send_to_os_trash(self, path: Path) -> bool:
        return False


class Env:
    def __init__(self, tmp: Path) -> None:
        self.inp = tmp / "in"
        self.out = tmp / "out"
        self.inp.mkdir()
        self.out.mkdir()
        self.engine: Engine = make_engine(":memory:")
        with DBSession(self.engine) as db:  # FK targets: one session, decisions 1..10
            db.add(SessionRow(id=1, input_root=str(self.inp), output_root=str(self.out)))
            db.commit()
            for i in range(1, 11):
                db.add(Decision(id=i, session_id=1, action="accept"))
            db.commit()
        self.platform = FakePlatform()
        self.settings = Settings(executor=ExecutorSettings(locked_retries=2, locked_backoff_s=0))
        self.ex = Executor(
            self.engine, 1, self.inp, self.out, cast(Any, self.platform), self.settings
        )

    def ops(self) -> list[Op]:
        with DBSession(self.engine) as db:
            return list(db.exec(select(Op).order_by(Op.id)))  # type: ignore[arg-type]


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path)
    yield e
    e.engine.dispose()


def snapshot(*roots: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    for root in roots:
        for p in sorted(root.rglob("*")):
            rel = root.name + "/" + p.relative_to(root).as_posix()
            if ".swipesort" in p.relative_to(root).parts:
                continue
            snap[rel] = "d" if p.is_dir() else blake3(p.read_bytes()).hexdigest()
    return snap


def test_move_file(env: Env) -> None:
    f = env.inp / "a.txt"
    f.write_text("hello")
    dst = env.ex.move(f, env.out / "Docs")
    assert dst == env.out / "Docs" / "a.txt"
    assert dst.read_text() == "hello" and not f.exists()
    ops = env.ops()
    assert [o.type for o in ops] == [OpType.mkdir, OpType.move]
    assert all(o.status == OpStatus.done for o in ops)
    assert ops[1].src == str(f) and ops[1].dst == str(dst)


def test_move_bundle_dir_as_unit(env: Env) -> None:
    b = env.inp / "proj"
    (b / ".git").mkdir(parents=True)
    (b / ".git" / "HEAD").write_text("ref")
    (b / "src").mkdir()
    (b / "src" / "main.py").write_text("print(1)")
    (b / "package.json").write_text("{}")
    before = snapshot(b)
    dst = env.ex.move(b, env.out / "Code")
    assert dst == env.out / "Code" / "proj"
    assert not b.exists()
    assert snapshot(dst) == {k.replace("proj/", "proj/", 1): v for k, v in before.items()} or (
        {k.split("/", 1)[1]: v for k, v in snapshot(dst).items()}
        == {k.split("/", 1)[1]: v for k, v in before.items()}
    )
    assert (dst / ".git" / "HEAD").read_text() == "ref"
    assert [o.type for o in env.ops() if o.type == OpType.move] == [OpType.move]


def test_move_dir_into_itself_refused(env: Env) -> None:
    d = env.inp / "d"
    d.mkdir()
    with pytest.raises(ExecError):
        env.ex.move(d, d / "sub")
    assert env.ops() == []


def test_collision_suffix(env: Env) -> None:
    (env.out / "Docs").mkdir()
    (env.out / "Docs" / "Report.pdf").write_text("old")
    f = env.inp / "report.pdf"
    f.write_text("new")
    dst = env.ex.move(f, env.out / "Docs")
    assert dst.name == "report (2).pdf"
    assert (env.out / "Docs" / "Report.pdf").read_text() == "old"
    g = env.inp / "REPORT.PDF"
    g.write_text("newer")
    assert env.ex.move(g, env.out / "Docs").name == "REPORT (3).PDF"


def test_mkdir_sanitizes_and_records_only_when_created(env: Env) -> None:
    p = env.ex.mkdir(env.out / "Q1: Taxes?")
    assert p == env.out / "Q1_ Taxes_" and p.is_dir()
    assert len(env.ops()) == 1
    assert env.ex.mkdir(env.out / "Q1: Taxes?") == p
    assert env.ex.mkdir(env.out / "q1_ taxes_") == p  # case-insensitive reuse
    assert len(env.ops()) == 1
    assert env.ex.mkdir(env.out / "CON").name == "CON_"


def test_move_reuses_existing_dir_without_op(env: Env) -> None:
    (env.out / "Docs").mkdir()
    f = env.inp / "a.txt"
    f.write_text("x")
    env.ex.move(f, env.out / "docs")
    assert [o.type for o in env.ops()] == [OpType.move]


def test_trash_layout(env: Env) -> None:
    (env.inp / "sub").mkdir()
    f = env.inp / "sub" / "junk.txt"
    f.write_text("junk")
    dst = env.ex.trash(f, decision_id=7)
    assert dst == env.out / ".swipesort" / "trash" / "1" / "sub" / "junk.txt"
    assert dst.read_text() == "junk" and not f.exists()
    last = env.ops()[-1]
    assert last.type == OpType.trash and last.status == OpStatus.done
    # from the output root, and collisions
    g = env.out / "x" / "junk.txt"
    g.parent.mkdir()
    g.write_text("2")
    assert env.ex.trash(g).parent == env.out / ".swipesort" / "trash" / "1" / "x"
    h = env.inp / "sub" / "junk.txt"
    h.write_text("3")
    assert env.ex.trash(h).name == "junk (2).txt"


def test_undo_one_decision_restores(env: Env) -> None:
    a = env.inp / "a.txt"
    b = env.inp / "b.txt"
    a.write_text("A")
    b.write_text("B")
    before = snapshot(env.inp, env.out)
    env.ex.move(a, env.out / "X", decision_id=1)
    env.ex.move(b, env.out / "Y", decision_id=2)
    undone = env.ex.undo(1)
    assert len(undone) == 2  # the move and its mkdir
    assert b.read_text() == "B" and not (env.out / "Y").exists()
    assert (env.out / "X" / "a.txt").exists()
    env.ex.undo(1)
    assert snapshot(env.inp, env.out) == before


def test_undo_decision_with_created_dirs_removes_them(env: Env) -> None:
    f = env.inp / "a.txt"
    f.write_text("A")
    env.ex.move(f, env.out / "A" / "B" / "C", decision_id=5)
    assert env.ex.undo(1)
    assert f.read_text() == "A"
    assert list(env.out.iterdir()) == []
    with DBSession(env.engine) as db:
        assert journal.done_ops_for_undo(db, 1, 10) == []
        assert {o.status for o in db.exec(select(Op))} == {OpStatus.undone}


def test_undo_trash_leaves_only_app_dir(env: Env) -> None:
    (env.inp / "s").mkdir()
    f = env.inp / "s" / "t.txt"
    f.write_text("t")
    env.ex.trash(f, decision_id=3)
    env.ex.undo(1)
    assert f.read_text() == "t"
    leftovers = [p for p in env.out.rglob("*") if p.is_dir() or p.is_file()]
    assert all(".swipesort" in p.relative_to(env.out).parts for p in leftovers)
    assert not any(p.is_file() for p in leftovers)
    assert not (env.out / ".swipesort" / "trash" / "1").exists()


def test_undo_groups_none_decision_separately(env: Env) -> None:
    a, b = env.inp / "a", env.inp / "b"
    a.write_text("1")
    b.write_text("2")
    env.ex.move(a, env.out)
    env.ex.move(b, env.out)
    env.ex.undo(1)
    assert b.exists() and not a.exists()
    env.ex.undo_all()
    assert a.exists()


def test_outside_roots_refused(env: Env, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.txt").write_text("x")
    f = env.inp / "f.txt"
    f.write_text("f")
    with pytest.raises(OutsideRootsError):
        env.ex.move(other / "x.txt", env.out)
    with pytest.raises(OutsideRootsError):
        env.ex.move(f, other)
    with pytest.raises(OutsideRootsError):
        env.ex.move(f, env.out / ".." / "other")
    with pytest.raises(OutsideRootsError):
        env.ex.trash(other / "x.txt")
    with pytest.raises(OutsideRootsError):
        env.ex.mkdir(other / "new")
    assert f.exists() and not (other / "new").exists()


def test_symlink_refused(env: Env, tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("t")
    link = env.inp / "link.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(SymlinkError):
        env.ex.move(link, env.out)
    with pytest.raises(SymlinkError):
        env.ex.trash(link)
    realdir = env.inp / "real"
    realdir.mkdir()
    (realdir / "f.txt").write_text("f")
    os.symlink(realdir, env.inp / "dirlink", target_is_directory=True)
    with pytest.raises(SymlinkError):
        env.ex.move(env.inp / "dirlink" / "f.txt", env.out)
    with pytest.raises(SymlinkError):
        env.ex.move(realdir / "f.txt", env.inp / "dirlink")
    assert target.exists() and link.is_symlink()


def test_locked_file_raises(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    f = env.inp / "a.txt"
    f.write_text("a")
    calls = {"n": 0}

    def boom(*a: object, **k: object) -> None:
        calls["n"] += 1
        raise PermissionError("locked")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(LockedFileError):
        env.ex.move(f, env.out)
    assert calls["n"] == 3  # 1 + locked_retries
    assert f.exists()
    assert env.ops()[-1].status == OpStatus.failed


def test_locked_via_platform(env: Env) -> None:
    f = env.inp / "a.txt"
    f.write_text("a")
    env.platform.locked = True
    with pytest.raises(LockedFileError):
        env.ex.move(f, env.out)
    env.platform.locked = False
    env.ex.move(f, env.out)
    assert (env.out / "a.txt").exists()


def test_cross_volume_copy_verify(env: Env) -> None:
    env.platform.same = False
    f = env.inp / "big.bin"
    f.write_bytes(os.urandom(5000))
    digest = blake3(f.read_bytes()).hexdigest()
    dst = env.ex.move(f, env.out / "Z")
    assert blake3(dst.read_bytes()).hexdigest() == digest and not f.exists()
    d = env.inp / "bundle"
    (d / "n").mkdir(parents=True)
    (d / "n" / "x").write_bytes(b"xx")
    (d / "empty").mkdir()
    dd = env.ex.move(d, env.out / "Z")
    assert (dd / "n" / "x").read_bytes() == b"xx" and (dd / "empty").is_dir() and not d.exists()
    env.ex.undo_all()
    assert f.read_bytes() and (d / "n" / "x").read_bytes() == b"xx"


def test_cross_volume_mismatch_keeps_source(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.platform.same = False
    f = env.inp / "a.bin"
    f.write_bytes(b"original")

    def bad_copy(s: Any, d: Any, **k: Any) -> Any:
        Path(d).write_bytes(b"corrupt!")
        return d

    monkeypatch.setattr(shutil, "copy2", bad_copy)
    with pytest.raises(ExecError, match="checksum"):
        env.ex.move(f, env.out)
    assert f.read_bytes() == b"original"
    assert not (env.out / "a.bin").exists()
    assert env.ops()[-1].status == OpStatus.failed


def test_recover(env: Env) -> None:
    done_src, done_dst = env.inp / "d.txt", env.out / "d.txt"
    done_dst.write_text("d")  # moved, crash before finish
    never_src, never_dst = env.inp / "n.txt", env.out / "n.txt"
    never_src.write_text("n")  # crash before move
    with DBSession(env.engine) as db:
        o1 = journal.begin(db, 1, OpType.move, done_src, done_dst, None)
        o2 = journal.begin(db, 1, OpType.move, never_src, never_dst, None)
        o3 = journal.begin(db, 1, OpType.mkdir, "", env.out / "made", None)
        (env.out / "made").mkdir()
        ids = (o1.id, o2.id, o3.id)
    env.ex.recover()
    status = {o.id: o.status for o in env.ops()}
    assert status[ids[0]] == OpStatus.done
    assert status[ids[1]] == OpStatus.failed
    assert status[ids[2]] == OpStatus.done
    env.ex.undo_all()
    assert done_src.read_text() == "d" and not (env.out / "made").exists()


def test_purge_trash(env: Env) -> None:
    from datetime import UTC, datetime, timedelta

    old, new = env.inp / "old.txt", env.inp / "new.txt"
    old.write_text("o")
    new.write_text("n")
    old_dst = env.ex.trash(old)
    new_dst = env.ex.trash(new)
    with DBSession(env.engine) as db:
        op = db.exec(select(Op).where(Op.dst == str(old_dst))).one()
        op.ts = datetime.now(UTC) - timedelta(days=40)
        db.add(op)
        db.commit()
    assert env.ex.purge_trash() == 1
    assert not old_dst.exists() and new_dst.exists()
    assert env.ex.purge_trash(older_than_days=0) == 1
    assert not new_dst.exists()
    assert not (env.out / ".swipesort" / "trash" / "1").exists()


# ----------------------------------------------------------------- property test

_DIRS = ["A", "b", "c", "A/x", "b/y"]
_FILES = ["f1.txt", "F2.txt", "g.pdf", "h.md"]
_DESTS = ["X", "x/Y", "a", "B/z", "N:ew", "A/x", "q/r/s"]


@st.composite
def _tree(draw: st.DrawFn) -> list[tuple[str, str, bytes]]:
    n = draw(st.integers(1, 8))
    seen: set[str] = set()
    out: list[tuple[str, str, bytes]] = []
    for _ in range(n):
        root = draw(st.sampled_from(["in", "out"]))
        d = draw(st.sampled_from([""] + _DIRS))
        f = draw(st.sampled_from(_FILES))
        key = f"{root}/{d}/{f}".casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append((root, f"{d}/{f}" if d else f, draw(st.binary(max_size=20))))
    return out


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    tree=_tree(),
    ops=st.lists(
        st.tuples(
            st.sampled_from(["move", "trash", "mkdir"]),
            st.integers(0, 50),
            st.sampled_from(["in", "out"]),
            st.sampled_from(_DESTS),
            st.integers(0, 3),
        ),
        max_size=12,
    ),
)
def test_property_undo_all_restores(
    tmp_path_factory: pytest.TempPathFactory,
    tree: list[tuple[str, str, bytes]],
    ops: list[tuple[str, int, str, str, int]],
) -> None:
    e = Env(tmp_path_factory.mktemp("prop"))
    try:
        roots = {"in": e.inp, "out": e.out}
        for root, rel, data in tree:
            p = roots[root] / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        before = snapshot(e.inp, e.out)
        for kind, idx, droot, dest, dec in ops:
            items = sorted(
                p
                for r in (e.inp, e.out)
                for p in r.rglob("*")
                if ".swipesort" not in p.relative_to(r).parts
            )
            decision = dec or None
            try:
                if kind == "mkdir":
                    e.ex.mkdir(roots[droot] / dest, decision)
                elif items:
                    item = items[idx % len(items)]
                    if kind == "move":
                        e.ex.move(item, roots[droot] / dest, decision)
                    else:
                        e.ex.trash(item, decision)
            except ExecError:
                pass
        e.ex.undo_all()
        assert snapshot(e.inp, e.out) == before
    finally:
        e.engine.dispose()
