"""New folders & cold start (SPEC §6): pure functions over numpy arrays. No DB, no IO.

- `cluster`: HDBSCAN on L2-normalized vectors (euclidean on unit vectors is monotone in cosine).
- `group_low_confidence` (§6.1): cluster the low-confidence pool per descent stop node.
- `decide_group` (§6.1 step 3): new folder vs `_Inbox` for one group.
- `cold_start_taxonomy` (§6.2): leaf clusters of the whole input, grouped into 5..12 top folders.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field

from swipesort.config import Proposals

Mat = npt.NDArray[np.float32]
Labels = npt.NDArray[np.int64]


def unit_rows(vecs: npt.ArrayLike) -> Mat:
    x = np.asarray(vecs, dtype=np.float32)
    if x.ndim == 1:
        x = x.reshape(1, -1)
    n = np.linalg.norm(x, axis=1, keepdims=True)
    n[n == 0] = 1.0
    out: Mat = x / n
    return out


def centroid(vecs: npt.ArrayLike) -> Mat:
    x = unit_rows(vecs)
    c = x.mean(axis=0)
    n = float(np.linalg.norm(c))
    out: Mat = (c / n if n > 0 else c).astype(np.float32)
    return out


def cohesion(vecs: npt.ArrayLike) -> float:
    """Mean pairwise cosine (1.0 for fewer than two vectors)."""
    x = unit_rows(vecs)
    n = x.shape[0]
    if n < 2:
        return 1.0
    s = x @ x.T
    return float((s.sum() - np.trace(s)) / (n * (n - 1)))


def max_sim(vec: npt.ArrayLike, others: Sequence[npt.ArrayLike]) -> float:
    """Max cosine of `vec` to any of `others` (-1.0 when there are none)."""
    if not others:
        return -1.0
    v = unit_rows(vec)[0]
    o = unit_rows(np.stack([np.asarray(x, dtype=np.float32) for x in others]))
    return float((o @ v).max())


def cluster(vecs: npt.ArrayLike, min_cluster_size: int, min_samples: int = 0,
            allow_single_cluster: bool = False) -> Labels:
    """HDBSCAN labels (-1 = noise). Too few points -> all noise."""
    x = unit_rows(vecs)
    n = x.shape[0]
    mcs = max(2, min_cluster_size)
    if n < mcs:
        return np.full(n, -1, dtype=np.int64)
    if n == mcs and allow_single_cluster and n <= 3:
        # HDBSCAN needs a little density to work with; a tiny pool is one candidate group
        return np.zeros(n, dtype=np.int64)
    from sklearn.cluster import HDBSCAN

    ms = min(min_samples, n) if min_samples > 0 else None
    model = HDBSCAN(min_cluster_size=mcs, min_samples=ms, metric="euclidean",
                    allow_single_cluster=allow_single_cluster, copy=True)
    labels: Labels = np.asarray(model.fit_predict(x.astype(np.float64)), dtype=np.int64)
    return labels


# --------------------------------------------------------------------------- §6.1 routing


class LowConf(BaseModel):
    """One low-confidence file in the pool. `key` = descent stop node (folder id)."""

    file_id: int
    key: int


class Group(BaseModel):
    key: int
    file_ids: list[int]
    cohesion: float


def group_low_confidence(items: Sequence[LowConf], vecs: Mat, cfg: Proposals
                         ) -> tuple[list[Group], list[LowConf]]:
    """Cluster the pool separately per stop node (files that stopped at different levels don't
    mix). Returns (groups, noise). `vecs[i]` belongs to `items[i]`."""
    groups: list[Group] = []
    noise: list[LowConf] = []
    by_key: dict[int, list[int]] = {}
    for i, it in enumerate(items):
        by_key.setdefault(it.key, []).append(i)
    for key in sorted(by_key):
        idx = by_key[key]
        sub = vecs[idx]
        labels = cluster(sub, cfg.min_cluster)
        k = int(labels.max()) + 1 if len(labels) else 0
        if k == 0 and len(idx) >= cfg.min_cluster and cohesion(sub) >= cfg.cohesion:
            # HDBSCAN is noise-happy on small pools: a cohesive pool is one group
            labels[:] = 0
            k = 1
        if k:  # attach noise to the nearest group when it is close enough
            cents = np.stack([centroid(sub[labels == lab]) for lab in range(k)])
            for j in np.where(labels < 0)[0].tolist():
                sims = cents @ unit_rows(sub[j])[0]
                best = int(np.argmax(sims))
                if float(sims[best]) >= cfg.noise_attach:
                    labels[j] = best
        parts = [[j for j in range(len(idx)) if labels[j] == lab] for lab in range(k)]
        noise.extend(items[idx[j]] for j in range(len(idx)) if labels[j] < 0)
        for part in merge_groups(sub, parts, cfg.group_merge_sim, cfg.cohesion):
            members = [idx[j] for j in part]
            groups.append(Group(key=key, file_ids=[items[m].file_id for m in members],
                                cohesion=cohesion(vecs[members])))
    return groups, noise


def merge_groups(x: Mat, parts: list[list[int]], min_centroid_sim: float,
                 min_cohesion: float) -> list[list[int]]:
    """Greedy agglomeration of HDBSCAN's (often over-split) groups: repeatedly merge the most
    similar pair while their centroids are >= min_centroid_sim apart and the union stays
    cohesive. Brute force: pools are small (SPEC scale: 100-1,000 files)."""
    parts = [list(p) for p in parts if p]
    while len(parts) > 1:
        cents = np.stack([centroid(x[p]) for p in parts])
        sims = cents @ cents.T
        np.fill_diagonal(sims, -2.0)
        best: tuple[int, int] | None = None
        for flat in np.argsort(-sims, axis=None).tolist():
            i, j = divmod(int(flat), len(parts))
            if i >= j:
                continue
            if float(sims[i, j]) < min_centroid_sim:
                break
            if cohesion(x[parts[i] + parts[j]]) >= min_cohesion:
                best = (i, j)
                break
        if best is None:
            break
        i, j = best
        parts[i] = sorted(parts[i] + parts[j])
        del parts[j]
    return parts


Verdict = Literal["new_folder", "inbox"]


def decide_group(n: int, group_cohesion: float, max_sibling: float, has_name: bool,
                 cfg: Proposals) -> Verdict:
    """SPEC §6.1 step 3: new folder only when big enough, cohesive, distinct from every sibling
    and specifically nameable; otherwise the stop node's `_Inbox`."""
    if (n >= cfg.min_cluster and group_cohesion >= cfg.cohesion
            and max_sibling < cfg.max_sibling_sim and has_name):
        return "new_folder"
    return "inbox"


def is_new_topic(max_folder_sim: float, has_name: bool, cfg: Proposals) -> bool:
    """A lone file far from every folder (and nameable) -> lower-priority new-folder card."""
    return max_folder_sim < cfg.singleton_new_topic and has_name


def overlaps(a: Sequence[int], b: Sequence[int], share: float) -> bool:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return False
    return len(sa & sb) / min(len(sa), len(sb)) >= share


# --------------------------------------------------------------------------- §6.2 cold start


class Taxonomy(BaseModel):
    """Indices refer to rows of the input matrix. `tops[t]` lists leaf indices."""

    leaves: list[list[int]] = Field(default_factory=list)
    tops: list[list[int]] = Field(default_factory=list)
    inbox: list[int] = Field(default_factory=list)


def _group_leaves(cents: Mat, cfg: Proposals) -> list[list[int]]:
    n = cents.shape[0]
    if n <= 1:
        return [[i] for i in range(n)]
    from sklearn.cluster import AgglomerativeClustering

    def fit(**kw: float | None) -> list[list[int]]:
        model = AgglomerativeClustering(metric="cosine", linkage="average", **kw)
        labels = model.fit_predict(cents.astype(np.float64))
        out: dict[int, list[int]] = {}
        for i, lab in enumerate(labels.tolist()):
            out.setdefault(int(lab), []).append(i)
        return list(out.values())

    groups = fit(n_clusters=None, distance_threshold=max(1e-6, 1.0 - cfg.top_merge_sim))
    if len(groups) > cfg.top_max:
        groups = fit(n_clusters=cfg.top_max)
    elif len(groups) < cfg.top_min and n >= cfg.top_min:
        groups = fit(n_clusters=cfg.top_min)
    # deterministic order: biggest first, then by first leaf
    return sorted((sorted(g) for g in groups), key=lambda g: g[0])


def cold_start_taxonomy(vecs: npt.ArrayLike, cfg: Proposals) -> Taxonomy:
    x = unit_rows(vecs)
    n = x.shape[0]
    if n == 0:
        return Taxonomy()
    mcs = max(cfg.min_cluster, round(cfg.cold_min_cluster_frac * n))
    labels = cluster(x, mcs, cfg.cold_min_samples)
    k = int(labels.max()) + 1 if n else 0
    leaves = [[i for i in range(n) if labels[i] == lab] for lab in range(k)]
    leaves = [lf for lf in leaves if lf]
    inbox: list[int] = []
    if leaves:
        cents = np.stack([centroid(x[lf]) for lf in leaves])
        for i in np.where(labels < 0)[0].tolist():
            sims = cents @ x[i]
            j = int(np.argmax(sims))
            if float(sims[j]) >= cfg.noise_attach:
                leaves[j].append(i)
            else:
                inbox.append(i)
        leaves = [sorted(lf) for lf in leaves]
        cents = np.stack([centroid(x[lf]) for lf in leaves])
        tops = _group_leaves(cents, cfg)
    else:
        inbox = list(range(n))
        tops = []
    # order tops (and leaves within) by size, largest first; reindex leaves accordingly
    tops = sorted(tops, key=lambda t: (-sum(len(leaves[i]) for i in t), t[0]))
    new_leaves: list[list[int]] = []
    new_tops: list[list[int]] = []
    for t in tops:
        ids: list[int] = []
        for li in sorted(t, key=lambda i: (-len(leaves[i]), i)):
            ids.append(len(new_leaves))
            new_leaves.append(leaves[li])
        new_tops.append(ids)
    return Taxonomy(leaves=new_leaves, tops=new_tops, inbox=sorted(inbox))
