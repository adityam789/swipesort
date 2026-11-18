"""Confidence model (SPEC §3): pure functions over numpy. No DB, no IO."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict

from swipesort.config import Scoring, Tiers

Vec = npt.NDArray[np.float32]

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class FolderIndex(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    folder_id: int
    path: str  # rel posix, "" = output root
    is_leaf: bool
    has_direct_files: bool
    centroid: np.ndarray | None
    profile: np.ndarray
    n_files: int
    ext_counts: dict[str, int]
    name_tokens: list[str]


class PlacedVec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    folder_id: int
    vec: np.ndarray


class Scored(BaseModel):
    folder_id: int
    path: str
    raw: float
    p: float
    signals: dict[str, float]


class FolderSpec(BaseModel):
    folder_id: int
    path: str  # rel posix, "" = root
    parent_id: int | None = None


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens (splits on punctuation, underscores, separators)."""
    return _TOKEN_RE.findall(text.lower())


def norm_ext(name_or_ext: str) -> str:
    s = name_or_ext.lower()
    if "." in s.lstrip("."):
        s = s.rsplit(".", 1)[1]
    return s.lstrip(".")


def _unit(v: npt.ArrayLike) -> Vec:
    a = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(a))
    return a / n if n > 0 else a


def _cos(a: npt.ArrayLike, b: npt.ArrayLike) -> float:
    x = np.asarray(a, dtype=np.float32)
    y = np.asarray(b, dtype=np.float32)
    if x.shape != y.shape:
        return 0.0
    nx, ny = float(np.linalg.norm(x)), float(np.linalg.norm(y))
    if nx == 0 or ny == 0:
        return 0.0
    return float(np.dot(x, y) / (nx * ny))


def candidates(folders: list[FolderIndex], leaf_only: bool) -> list[FolderIndex]:
    """Folders a file may land in.

    leaf_only: leaves plus folders that already hold files directly. Otherwise every folder.
    The output root ("") is excluded unless it is the only folder or has direct files.
    """
    if len(folders) == 1:
        return list(folders)
    out: list[FolderIndex] = []
    for f in folders:
        if f.path == "" and not f.has_direct_files:
            continue
        if leaf_only and not (f.is_leaf or f.has_direct_files):
            continue
        out.append(f)
    return out


def build_folder_index(
    folders: Sequence[FolderSpec],
    file_vecs: Mapping[int, Sequence[npt.ArrayLike]],
    file_names: Mapping[int, Sequence[str]],
    profiles: Mapping[int, npt.ArrayLike],
    dim: int | None = None,
) -> list[FolderIndex]:
    """Aggregate direct per-folder data (vecs, filenames) into subtree FolderIndex rows."""
    children: dict[int, list[int]] = {f.folder_id: [] for f in folders}
    for f in folders:
        if f.parent_id is not None and f.parent_id in children:
            children[f.parent_id].append(f.folder_id)
    if dim is None:
        for p in profiles.values():
            dim = int(np.asarray(p).shape[0])
            break
    zero = np.zeros(dim or 1, dtype=np.float32)

    def subtree(fid: int) -> list[int]:
        out, stack = [], [fid]
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(children[cur])
        return out

    result: list[FolderIndex] = []
    for f in folders:
        ids = subtree(f.folder_id)
        vecs = [_unit(v) for i in ids for v in file_vecs.get(i, ())]
        names = [n for i in ids for n in file_names.get(i, ())]
        direct = max(len(file_names.get(f.folder_id, ())), len(file_vecs.get(f.folder_id, ())))
        n_files = max(len(names), len(vecs))
        centroid = _unit(np.mean(np.stack(vecs), axis=0)) if vecs else None
        tokens = [t for n in names for t in tokenize(n.rsplit(".", 1)[0] if "." in n else n)]
        prof = profiles.get(f.folder_id)
        result.append(
            FolderIndex(
                folder_id=f.folder_id,
                path=f.path,
                is_leaf=not children[f.folder_id],
                has_direct_files=direct > 0,
                centroid=centroid,
                profile=_unit(prof) if prof is not None else zero,
                n_files=n_files,
                ext_counts=dict(Counter(norm_ext(n) for n in names if "." in n.lstrip("."))),
                name_tokens=tokens,
            )
        )
    return result


def _softmax(raw: npt.NDArray[np.float64], temperature: float) -> npt.NDArray[np.float64]:
    z = raw / max(temperature, 1e-6)
    z = z - z.max()
    e = np.exp(z)
    out: npt.NDArray[np.float64] = e / e.sum()
    return out


def _knn_shares(file_vec: Vec, placed: list[PlacedVec], k: int) -> dict[int, float]:
    """Similarity-weighted vote per folder among the k nearest placed vectors.

    Each of the k slots contributes its (clipped) cosine to its folder, divided by the number of
    slots. Unlike a pure vote *share*, weak neighbours give a weak signal: with two unrelated
    placed files a folder no longer gets 0.5 just for existing."""
    if not placed or k <= 0:
        return {}
    sims = np.array([_cos(file_vec, p.vec) for p in placed], dtype=np.float64)
    order = np.argsort(-sims, kind="stable")[:k]
    weights: dict[int, float] = {}
    for i in order:
        w = max(float(sims[i]), 0.0)
        weights[placed[int(i)].folder_id] = weights.get(placed[int(i)].folder_id, 0.0) + w
    slots = float(len(order))
    return {fid: w / slots for fid, w in weights.items()}


def _token_scores(
    file_tokens: list[str], folders: list[FolderIndex], k1: float, b: float
) -> list[float]:
    """BM25-lite of file tokens against each folder's (subtree filename + path) tokens, in [0,1]."""
    q = set(file_tokens)
    if not q or not folders:
        return [0.0] * len(folders)
    docs = [Counter(f.name_tokens + tokenize(f.path)) for f in folders]
    lens = [sum(d.values()) for d in docs]
    avg = max(sum(lens) / len(docs), 1.0)
    n = len(docs)
    max_score = 0.0
    idf: dict[str, float] = {}
    for t in q:
        df = sum(1 for d in docs if t in d)
        idf[t] = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        max_score += idf[t] * (k1 + 1.0)
    if max_score <= 0:
        return [0.0] * len(folders)
    out: list[float] = []
    for d, ln in zip(docs, lens, strict=True):
        s = 0.0
        for t in q:
            tf = d.get(t, 0)
            if tf:
                s += idf[t] * tf * (k1 + 1.0) / (tf + k1 * (1 - b + b * ln / avg))
        out.append(min(s / max_score, 1.0))
    return out


def _ext_share(ext: str, f: FolderIndex) -> float:
    if f.n_files <= 0 or not ext:
        return 0.0
    total = sum(f.ext_counts.values())
    if total <= 0:
        return 0.0
    return (f.ext_counts.get(ext, 0) + 1.0) / (total + len(f.ext_counts) + 1.0)  # Laplace


def score(
    file_vec: npt.ArrayLike,
    file_name_tokens: list[str],
    file_ext: str,
    folders: list[FolderIndex],
    placed: list[PlacedVec],
    cfg: Scoring,
) -> list[Scored]:
    """Score `folders` (already filtered by candidates()) for one file; sorted desc by p."""
    if not folders:
        return []
    vec = np.asarray(file_vec, dtype=np.float32)
    ext = norm_ext(file_ext)
    knn = _knn_shares(vec, placed, cfg.knn_k)
    tok = _token_scores(file_name_tokens, folders, cfg.bm25_k1, cfg.bm25_b)
    rows: list[tuple[FolderIndex, float, dict[str, float]]] = []
    # Missing evidence is neutral, not negative: an empty folder (cold start, or one the user
    # just created) has no centroid and no file types, so it borrows its profile similarity as
    # the centroid stand-in and the average extension prior of the folders that do have files.
    shares = [_ext_share(ext, f) for f in folders if f.n_files > 0]
    neutral_ext = sum(shares) / len(shares) if shares else 0.0
    for f, t in zip(folders, tok, strict=True):
        prof = _cos(vec, f.profile)
        cen = f.centroid if f.n_files > 0 else None
        ext_s = neutral_ext if f.n_files == 0 else _ext_share(ext, f)
        sig = {
            "centroid": prof if cen is None else _cos(vec, cen),
            "profile": prof,
            "knn": knn.get(f.folder_id, 0.0),
            "meta": cfg.meta_ext_share * ext_s + (1.0 - cfg.meta_ext_share) * t,
        }
        w_profile = cfg.w_profile_empty if f.n_files == 0 else cfg.w_profile
        raw = (
            cfg.w_centroid * sig["centroid"]
            + w_profile * sig["profile"]
            + cfg.w_knn * sig["knn"]
            + cfg.w_meta * sig["meta"]
        )
        rows.append((f, raw, sig))
    p = _softmax(np.array([r[1] for r in rows], dtype=np.float64), cfg.temperature)
    scored = [
        Scored(folder_id=f.folder_id, path=f.path, raw=raw, p=float(pi), signals=sig)
        for (f, raw, sig), pi in zip(rows, p, strict=True)
    ]
    scored.sort(key=lambda s: s.p, reverse=True)
    return scored


def tier(scored: list[Scored], cfg: Tiers) -> tuple[str, float]:
    """("confirm"|"choices"|"inbox", confidence). Margin rule: p1 - p2 < min_margin blocks confirm."""
    if not scored:
        return "inbox", 0.0
    conf = scored[0].p
    p2 = scored[1].p if len(scored) > 1 else 0.0
    if conf >= cfg.confirm and conf - p2 >= cfg.min_margin:
        return "confirm", conf
    if conf >= cfg.choices:
        return "choices", conf
    return "inbox", conf
