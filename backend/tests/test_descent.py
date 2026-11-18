from __future__ import annotations

import numpy as np

from swipesort.config import Descent, Scoring, Tiers
from swipesort.engine.descent import DescentResult, descend
from swipesort.engine.scoring import FolderIndex, FolderSpec, PlacedVec, build_folder_index

DIM = 8
ROOT, WORK, HOME, PAY, ACME, TAX, PHOTOS, INBOX = 1, 2, 3, 4, 5, 6, 7, 8


def e(i: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def mix(i: int, j: int, a: float = 1.0, b: float = 1.0) -> np.ndarray:
    v = a * e(i) + b * e(j)
    return (v / np.linalg.norm(v)).astype(np.float32)


def build(
    specs: list[tuple[int, str, int | None]], vecs: dict[int, list[np.ndarray]]
) -> tuple[list[FolderIndex], dict[int, int | None]]:
    folders = [FolderSpec(folder_id=i, path=p, parent_id=par) for i, p, par in specs]
    names = {k: [f"f{n}.txt" for n in range(len(v))] for k, v in vecs.items()}
    idx = build_folder_index(folders, vecs, names, {}, dim=DIM)
    return idx, {i: par for i, _, par in specs}


TREE = [
    (ROOT, "", None),
    (WORK, "Work", ROOT),
    (HOME, "Home", ROOT),
    (PAY, "Work/Pay", WORK),
    (ACME, "Work/Acme", WORK),
    (TAX, "Home/Tax", HOME),
    (PHOTOS, "Home/Photos", HOME),
]
VECS = {
    PAY: [e(0)] * 3,
    ACME: [e(1)] * 3,
    TAX: [e(2)] * 3,
    PHOTOS: [e(3)] * 3,
}


def run(
    vec: np.ndarray,
    idx: list[FolderIndex],
    parents: dict[int, int | None],
    placed: list[PlacedVec] | None = None,
    descent: Descent | None = None,
    excluded: set[int] | None = None,
    tiers: Tiers | None = None,
) -> DescentResult:
    return descend(
        vec, [], "txt", idx, parents, ROOT, placed or [], Scoring(), tiers or Tiers(),
        descent or Descent(), excluded or set(),
    )


def test_descends_to_right_leaf() -> None:
    idx, parents = build(TREE, VECS)
    r = run(e(2), idx, parents)
    assert r.path == [HOME, TAX]
    assert r.reached_leaf and r.stop_folder_id == TAX
    assert len(r.step_confs) == 2 and all(c >= 0.75 for c in r.step_confs)
    assert abs(r.path_conf - r.step_confs[0] * r.step_confs[1]) < 1e-9


def test_ambiguous_level_two_stops_mid_tree() -> None:
    idx, parents = build(TREE, VECS)
    r = run(mix(0, 1), idx, parents)  # Work is clear; Pay vs Acme is a coin flip
    assert r.path == [WORK]
    assert r.stop_folder_id == WORK
    assert not r.reached_leaf
    assert {s.folder_id for s in r.stop_scores} == {PAY, ACME}
    assert r.stop_scores[0].p < 0.75
    assert r.path_conf == r.step_confs[0]


def test_path_conf_product_vs_min() -> None:
    idx, parents = build(TREE, VECS)
    v = mix(2, 3, 1.0, 0.3)  # Home clear, Tax over Photos moderately clear
    tiers = Tiers(descend=0.5)
    p = run(v, idx, parents, descent=Descent(path_conf="product"), tiers=tiers)
    m = run(v, idx, parents, descent=Descent(path_conf="min"), tiers=tiers)
    assert p.path == m.path == [HOME, TAX]
    assert m.path_conf == min(m.step_confs)
    assert abs(p.path_conf - float(np.prod(p.step_confs))) < 1e-9
    assert p.path_conf < m.path_conf


def test_excluded_never_chosen() -> None:
    specs = [*TREE, (INBOX, "Work/_Inbox", WORK)]
    vecs = {**VECS, INBOX: [e(0)] * 10}  # a very attractive inbox
    idx, parents = build(specs, vecs)
    r = run(e(0), idx, parents, excluded={INBOX})
    assert INBOX not in r.path
    assert r.path == [WORK, PAY]
    r2 = run(mix(0, 1), idx, parents, excluded={INBOX})
    assert INBOX not in {s.folder_id for s in r2.stop_scores}


def test_excluded_only_children_is_leaf() -> None:
    specs = [(ROOT, "", None), (WORK, "Work", ROOT), (INBOX, "Work/_Inbox", WORK)]
    idx, parents = build(specs, {WORK: [e(0)] * 2, INBOX: [e(0)]})
    r = run(e(0), idx, parents, excluded={INBOX})
    assert r.path == [WORK] and r.reached_leaf


def test_leaf_only_rule_non_root_with_direct_files() -> None:
    # Work holds direct files AND has children; ambiguous below -> Work is a real destination.
    vecs = {**VECS, WORK: [e(0), e(1)]}
    idx, parents = build(TREE, vecs)
    r = run(mix(0, 1), idx, parents)
    assert r.stop_folder_id == WORK and r.reached_leaf
    assert r.stop_scores  # still reports sibling scores
    # Without direct files the same stop is not a destination.
    idx2, parents2 = build(TREE, VECS)
    assert not run(mix(0, 1), idx2, parents2).reached_leaf


def test_root_edge_cases() -> None:
    idx, parents = build([(ROOT, "", None)], {})
    r = run(e(0), idx, parents)
    assert r.path == [] and r.path_conf == 1.0 and r.stop_folder_id == ROOT
    assert not r.reached_leaf and r.stop_scores == []
    idx, parents = build([(ROOT, "", None)], {ROOT: [e(0)]})
    assert run(e(0), idx, parents).reached_leaf
    # Ambiguous at the root: stop at root, not a destination even with direct files.
    idx, parents = build(TREE, {**VECS, ROOT: [e(5)]})
    r = run(mix(0, 2), idx, parents)
    assert r.path == [] and r.stop_folder_id == ROOT and not r.reached_leaf


def test_knn_counts_descendants_of_each_child() -> None:
    # Subtree signals are identical/empty; only placed vectors (in deep descendants) differ.
    idx, parents = build(TREE, {})
    placed = [PlacedVec(folder_id=PAY, vec=e(4)) for _ in range(4)]
    placed += [PlacedVec(folder_id=TAX, vec=e(5)) for _ in range(4)]
    r = run(e(4), idx, parents, placed=placed, tiers=Tiers(descend=0.6))
    assert r.path[0] == WORK  # PAY placed vecs vote for their ancestor WORK
    r2 = run(e(5), idx, parents, placed=placed, tiers=Tiers(descend=0.6))
    assert r2.path[0] == HOME


def test_placed_outside_subtree_ignored_at_lower_level() -> None:
    idx, parents = build(TREE, {})
    # Placed vecs in Home/ must not influence the choice between Pay and Acme.
    placed = [PlacedVec(folder_id=TAX, vec=e(4)) for _ in range(5)]
    placed.append(PlacedVec(folder_id=ACME, vec=e(4)))
    r = run(e(4), idx, parents, placed=placed, tiers=Tiers(descend=0.0))
    # Level 1 may go either way; force Work and check level 2 sees only Acme's vote.
    r = descend(e(4), [], "txt", idx, parents, WORK, placed, Scoring(), Tiers(descend=0.0),
                Descent(), set())
    assert r.path[0] == ACME
