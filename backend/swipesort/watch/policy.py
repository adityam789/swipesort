"""What happens to a newly scored file in a watched folder. Pure function."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from swipesort.config import Watch

WatchAction = Literal["move", "trash", "pending"]


class Verdict(BaseModel):
    action: WatchAction
    why: str


def auto_unlocked(cfg: Watch, decisions: int) -> bool:
    """Safety: no automatic action until the user made enough decisions for the
    confidence calibration to mean something."""
    return decisions >= cfg.min_decisions_for_auto


def decide(cfg: Watch, *, decisions: int, tier: str | None, confidence: float,
           dup_relation: str | None, dup_in_output: bool) -> Verdict:
    """
    - exact duplicate of a file already in the output tree -> quarantine when
      `auto_trash_exact_dupes` (off by default), else a pending Duplicate card
    - any other duplicate relation -> pending (the user picks which copy to keep)
    - Confirm tier at >= `auto_move_threshold` -> move automatically
    - everything else -> pending queue (normal cards in the watch session)
    Auto actions are gated by `auto_move` and the minimum-decisions safety rule.
    """
    unlocked = auto_unlocked(cfg, decisions)
    if dup_relation is not None:
        if dup_relation == "exact" and dup_in_output and cfg.auto_trash_exact_dupes:
            if not unlocked:
                return Verdict(action="pending", why="auto actions locked (too few decisions)")
            return Verdict(action="trash", why="exact duplicate of a file in the output tree")
        return Verdict(action="pending", why=f"duplicate ({dup_relation})")
    if tier != "confirm" or confidence < cfg.auto_move_threshold:
        return Verdict(action="pending", why=f"{tier or 'no'} tier at {confidence:.2f}")
    if not cfg.auto_move:
        return Verdict(action="pending", why="auto-move is off")
    if not unlocked:
        return Verdict(action="pending", why="auto-move locked (too few decisions)")
    return Verdict(action="move", why=f"confirm tier at {confidence:.2f}")
