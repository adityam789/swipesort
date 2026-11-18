"""Batch cards (SPEC §1 #4, §2.2 Batch row). Pure.

CONTRACT (implemented by the descent agent):

class BatchItem(BaseModel): file_id: int; tier: str; confidence: float;
    target_folder_id: int | None   # top candidate / destination
    stop_folder_id: int | None; reached_leaf: bool; rank: int  # card order

class BatchGroup(BaseModel): kind: Literal["move", "inbox"]; target_folder_id: int;
    file_ids: list[int]; min_confidence: float; rank: int  # rank of first member

def make_batches(items: list[BatchItem], cfg: Batch) -> list[BatchGroup]
    # "move": confirm-tier items with confidence >= cfg.threshold sharing target_folder_id,
    #   group size >= cfg.min_size.
    # "inbox": inbox-tier items (descent stopped mid-tree / low confidence) sharing
    #   stop_folder_id, size >= cfg.inbox_min_size -> target = stop folder (its _Inbox).
    # Each file in at most one group. Groups ordered by rank. Members ordered by rank.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from swipesort.config import Batch


class BatchItem(BaseModel):
    file_id: int
    tier: str
    confidence: float
    target_folder_id: int | None
    stop_folder_id: int | None
    reached_leaf: bool
    rank: int


class BatchGroup(BaseModel):
    kind: Literal["move", "inbox"]
    target_folder_id: int
    file_ids: list[int]
    min_confidence: float
    rank: int


def _group(
    kind: Literal["move", "inbox"],
    buckets: dict[int, list[BatchItem]],
    min_size: int,
) -> list[BatchGroup]:
    out: list[BatchGroup] = []
    for target, members in buckets.items():
        if len(members) < max(min_size, 1):
            continue
        members = sorted(members, key=lambda i: (i.rank, i.file_id))
        out.append(
            BatchGroup(
                kind=kind,
                target_folder_id=target,
                file_ids=[m.file_id for m in members],
                min_confidence=min(m.confidence for m in members),
                rank=members[0].rank,
            )
        )
    return out


def make_batches(items: list[BatchItem], cfg: Batch) -> list[BatchGroup]:
    used: set[int] = set()
    seen: set[int] = set()
    unique: list[BatchItem] = []
    for it in items:
        if it.file_id not in seen:
            seen.add(it.file_id)
            unique.append(it)
    items = unique
    move: dict[int, list[BatchItem]] = {}
    for it in items:
        if (
            it.tier == "confirm"
            and it.confidence >= cfg.threshold
            and it.target_folder_id is not None
            and it.file_id not in used
        ):
            move.setdefault(it.target_folder_id, []).append(it)
    groups = _group("move", move, cfg.min_size)
    for g in groups:
        used.update(g.file_ids)
    inbox: dict[int, list[BatchItem]] = {}
    for it in items:
        if it.tier == "inbox" and it.stop_folder_id is not None and it.file_id not in used:
            inbox.setdefault(it.stop_folder_id, []).append(it)
    groups += _group("inbox", inbox, cfg.inbox_min_size)
    groups.sort(key=lambda g: (g.rank, g.kind, g.target_folder_id))
    return groups
