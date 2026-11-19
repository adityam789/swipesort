"""Journal helpers: Op rows written intent -> done/failed so every mutation can be undone."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from swipesort.db.models import Op, OpStatus, OpType


def begin(
    db: DBSession,
    session_id: int,
    type: OpType,
    src: Path | str,
    dst: Path | str,
    decision_id: int | None,
) -> Op:
    """Write an `intent` row and commit it BEFORE the filesystem action."""
    op = Op(
        session_id=session_id,
        decision_id=decision_id,
        type=type,
        src=str(src),
        dst=str(dst),
        status=OpStatus.intent,
    )
    db.add(op)
    db.commit()
    db.refresh(op)
    return op


def _set(db: DBSession, op: Op, status: OpStatus, error: str = "") -> None:
    op.status = status
    if error:
        op.error = error
    db.add(op)
    db.commit()


def finish(db: DBSession, op: Op) -> None:
    _set(db, op, OpStatus.done)


def fail(db: DBSession, op: Op, err: object) -> None:
    _set(db, op, OpStatus.failed, str(err) or type(err).__name__)


def mark_undone(db: DBSession, op: Op) -> None:
    _set(db, op, OpStatus.undone)


def done_ops_for_undo(db: DBSession, session_id: int, steps: int) -> list[list[Op]]:
    """Done ops grouped per decision (decision_id None -> own group), latest group first.

    Within each group ops are ordered latest first, i.e. ready to be reversed LIFO.
    """
    stmt = (
        select(Op)
        .where(col(Op.session_id) == session_id, col(Op.status) == OpStatus.done)
        .order_by(col(Op.id).desc())
    )
    groups: list[list[Op]] = []
    by_decision: dict[int, list[Op]] = {}
    for op in db.exec(stmt):
        if op.decision_id is None:
            groups.append([op])
        elif op.decision_id in by_decision:
            by_decision[op.decision_id].append(op)
        else:
            g = [op]
            by_decision[op.decision_id] = g
            groups.append(g)
    return groups[: max(steps, 0)]


def done_ops_of_decision(db: DBSession, session_id: int, decision_id: int) -> list[Op]:
    """Done ops of one decision, latest first (ready to be reversed LIFO)."""
    stmt = (
        select(Op)
        .where(col(Op.session_id) == session_id, col(Op.decision_id) == decision_id,
               col(Op.status) == OpStatus.done)
        .order_by(col(Op.id).desc())
    )
    return list(db.exec(stmt))
