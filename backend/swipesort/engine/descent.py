"""Confidence-gated descent (SPEC §4). Pure functions over the folder index.

CONTRACT (implemented by the descent agent):

class DescentResult(BaseModel):
    path: list[int]            # folder ids from (excluding) root down to the stop node
    step_confs: list[float]    # p of the chosen child at each step
    path_conf: float           # product or min of step_confs (cfg.path_conf); 1.0 if no steps
    stop_folder_id: int        # node where descent stopped (a leaf, or mid-tree)
    reached_leaf: bool
    stop_scores: list[Scored]  # sibling scores at the step where it stopped (for "Probably
                               # somewhere in X/, unsure below that" + alternatives)

def descend(file_vec, file_name_tokens: list[str], file_ext: str,
            index: list[FolderIndex], parents: dict[int, int | None], root_id: int,
            placed: list[PlacedVec], scoring: Scoring, tiers: Tiers, descent: Descent,
            excluded: set[int]) -> DescentResult
    # loop: children of node (excluding `excluded`, e.g. _Inbox folders); none -> stop (leaf);
    # score children with scoring.score() (softmax over siblings, using SUBTREE aggregates —
    # FolderIndex already holds subtree centroid/ext/name tokens); if best p >= tiers.descend:
    # step into it; else stop at node. Leaf-only rule (SPEC §4): if node has_direct_files and
    # node is not root, stopping there is a real destination (reached_leaf=True).
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from swipesort.config import Descent, Scoring, Tiers
from swipesort.engine.scoring import FolderIndex, PlacedVec, Scored, score


class DescentResult(BaseModel):
    path: list[int]
    step_confs: list[float]
    path_conf: float
    stop_folder_id: int
    reached_leaf: bool
    stop_scores: list[Scored]


def _child_of(folder_id: int, node: int, parents: dict[int, int | None]) -> int | None:
    """Ancestor-or-self of `folder_id` whose parent is `node`; None if outside node's subtree."""
    cur: int | None = folder_id
    seen: set[int] = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        par = parents.get(cur)
        if par == node:
            return cur
        cur = par
    return None


def _remap_placed(
    placed: list[PlacedVec], node: int, parents: dict[int, int | None]
) -> list[PlacedVec]:
    out: list[PlacedVec] = []
    for p in placed:
        child = _child_of(p.folder_id, node, parents)
        if child is not None:
            out.append(PlacedVec(folder_id=child, vec=p.vec))
    return out


def descend(
    file_vec: npt.ArrayLike,
    file_name_tokens: list[str],
    file_ext: str,
    index: list[FolderIndex],
    parents: dict[int, int | None],
    root_id: int,
    placed: list[PlacedVec],
    scoring: Scoring,
    tiers: Tiers,
    descent: Descent,
    excluded: set[int],
    null_raw: float | None = None,
) -> DescentResult:
    vec = np.asarray(file_vec, dtype=np.float32)
    by_id = {f.folder_id: f for f in index}
    node = root_id
    path: list[int] = []
    step_confs: list[float] = []
    stop_scores: list[Scored] = []
    visited = {root_id}
    reached_leaf = False

    while True:
        children = [
            f
            for f in index
            if f.folder_id not in excluded
            and f.folder_id != node
            and parents.get(f.folder_id) == node
            and f.folder_id not in visited
        ]
        node_info = by_id.get(node)
        has_direct = node_info.has_direct_files if node_info is not None else False
        if not children:
            reached_leaf = node != root_id or has_direct
            stop_scores = []
            break
        scored = score(
            vec, file_name_tokens, file_ext, children, _remap_placed(placed, node, parents),
            scoring, null_raw,
        )
        best = scored[0]
        if best.p >= tiers.descend:
            node = best.folder_id
            visited.add(node)
            path.append(node)
            step_confs.append(best.p)
            continue
        stop_scores = scored
        reached_leaf = node != root_id and has_direct
        break

    if not step_confs:
        path_conf = 1.0
    elif descent.path_conf == "min":
        path_conf = min(step_confs)
    else:
        path_conf = float(np.prod(step_confs))
    return DescentResult(
        path=path,
        step_confs=step_confs,
        path_conf=path_conf,
        stop_folder_id=node,
        reached_leaf=reached_leaf,
        stop_scores=stop_scores,
    )


def descend_mass(
    flat: list[Scored],
    parents: dict[int, int | None],
    root_id: int,
    tiers: Tiers,
    descent: Descent,
    has_direct: set[int],
) -> DescentResult:
    """Confidence-gated descent over flat leaf probabilities (descent.method = "mass").

    A folder's confidence is the probability mass of all candidate leaves in its subtree
    (`flat` comes from scoring.score over leaf candidates, incl. the "none of these" option, so
    masses sum to <= 1). Step into the child holding >= tiers.descend of the mass; stop where it
    splits. Same semantics as SPEC §4 (descend while confident, else <node>/_Inbox) but the
    destination never contradicts the flat ranking, and coarse subtree centroids aren't needed.
    """
    p_leaf = {s.folder_id: s.p for s in flat}
    by_id = {s.folder_id: s for s in flat}
    children: dict[int, list[int]] = {}
    for fid, par in parents.items():
        if par is not None:
            children.setdefault(par, []).append(fid)
    mass: dict[int, float] = {}

    def subtree_mass(fid: int) -> float:
        if fid not in mass:
            mass[fid] = p_leaf.get(fid, 0.0) + sum(subtree_mass(c) for c in children.get(fid, []))
        return mass[fid]

    node, path, steps = root_id, [], []
    while True:
        kids = [c for c in children.get(node, []) if subtree_mass(c) > 0.0]
        options: list[tuple[float, int | None]] = [(subtree_mass(c), c) for c in kids]
        if node != root_id and node in has_direct:
            options.append((p_leaf.get(node, 0.0), None))  # "stay here" (leaf-only rule)
        if not options:
            reached = node != root_id or node in has_direct
            break
        best_mass, best = max(options, key=lambda o: o[0])
        if best is None:  # staying at a node that keeps files directly
            reached = best_mass >= tiers.descend
            break
        if best_mass < tiers.descend:
            reached = False
            break
        parent_mass = subtree_mass(node) if node != root_id else 1.0
        steps.append(best_mass / parent_mass if parent_mass > 0 else 0.0)
        path.append(best)
        node = best
    if children.get(node) and node not in has_direct:
        reached = False  # a folder with subfolders and no files of its own isn't a destination
    stop_scores = sorted((by_id[c] for c in _leaves_under(node, children) if c in by_id),
                         key=lambda s: s.p, reverse=True)
    conf = subtree_mass(node) if node != root_id else 0.0
    return DescentResult(path=path, step_confs=steps,
                         path_conf=conf if reached else min(conf, max(steps, default=0.0)),
                         stop_folder_id=node, reached_leaf=reached, stop_scores=stop_scores)


def _leaves_under(node: int, children: dict[int, list[int]]) -> list[int]:
    out, stack = [], [node]
    while stack:
        cur = stack.pop()
        out.append(cur)
        stack.extend(children.get(cur, []))
    return out
