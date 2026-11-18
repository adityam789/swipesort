from __future__ import annotations

from swipesort.config import Batch
from swipesort.engine.batching import BatchItem, make_batches


def move(fid: int, target: int, rank: int, conf: float = 0.97) -> BatchItem:
    return BatchItem(file_id=fid, tier="confirm", confidence=conf, target_folder_id=target,
                     stop_folder_id=target, reached_leaf=True, rank=rank)


def inbox(fid: int, stop: int, rank: int, conf: float = 0.3) -> BatchItem:
    return BatchItem(file_id=fid, tier="inbox", confidence=conf, target_folder_id=None,
                     stop_folder_id=stop, reached_leaf=False, rank=rank)


CFG = Batch()  # min_size 3, threshold 0.95, inbox_min_size 3


def test_move_group_needs_min_size_and_threshold() -> None:
    items = [move(1, 10, 0), move(2, 10, 1), move(3, 10, 2, conf=0.94)]
    assert make_batches(items, CFG) == []  # only 2 pass the threshold
    items.append(move(4, 10, 3))
    (g,) = make_batches(items, CFG)
    assert g.kind == "move" and g.target_folder_id == 10
    assert g.file_ids == [1, 2, 4] and g.min_confidence == 0.97 and g.rank == 0


def test_only_confirm_tier_moves() -> None:
    items = [move(i, 10, i) for i in range(3)]
    items[1] = items[1].model_copy(update={"tier": "choices"})
    assert make_batches(items, CFG) == []


def test_mixed_targets_split() -> None:
    items = [move(1, 10, 0), move(2, 20, 1), move(3, 10, 2), move(4, 20, 3), move(5, 10, 4),
             move(6, 20, 5), move(7, 30, 6)]
    groups = make_batches(items, CFG)
    assert [(g.target_folder_id, g.file_ids) for g in groups] == [(10, [1, 3, 5]), (20, [2, 4, 6])]
    assert [g.rank for g in groups] == [0, 1]


def test_inbox_groups_by_stop_node() -> None:
    items = [inbox(1, 5, 0), inbox(2, 6, 1), inbox(3, 5, 2), inbox(4, 5, 3), inbox(5, 6, 4)]
    (g,) = make_batches(items, CFG)
    assert g.kind == "inbox" and g.target_folder_id == 5 and g.file_ids == [1, 3, 4]
    assert g.min_confidence == 0.3
    groups = make_batches(items, Batch(inbox_min_size=2))
    assert [(x.target_folder_id, x.file_ids) for x in groups] == [(5, [1, 3, 4]), (6, [2, 5])]


def test_each_file_once_and_ordering_by_rank() -> None:
    items = [inbox(9, 5, 7), move(3, 10, 5), inbox(8, 5, 4), move(2, 10, 3), move(1, 10, 1),
             inbox(7, 5, 2), move(1, 10, 99)]  # file 1 duplicated
    groups = make_batches(items, CFG)
    flat = [f for g in groups for f in g.file_ids]
    assert len(flat) == len(set(flat)) == 6
    assert [g.rank for g in groups] == sorted(g.rank for g in groups) == [1, 2]
    assert groups[0].kind == "move" and groups[0].file_ids == [1, 2, 3]
    assert groups[1].kind == "inbox" and groups[1].file_ids == [7, 8, 9]


def test_missing_targets_skipped() -> None:
    items = [BatchItem(file_id=i, tier="confirm", confidence=0.99, target_folder_id=None,
                       stop_folder_id=None, reached_leaf=False, rank=i) for i in range(5)]
    assert make_batches(items, CFG) == []
