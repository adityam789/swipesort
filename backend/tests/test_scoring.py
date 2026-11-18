from __future__ import annotations

import numpy as np

from swipesort.config import Scoring, Settings, Tiers
from swipesort.engine.scoring import (
    FolderIndex,
    FolderSpec,
    PlacedVec,
    Scored,
    build_folder_index,
    candidates,
    score,
    tier,
    tokenize,
)
from swipesort.ml.providers.fake import FakeProvider


def e(i: int, dim: int = 8, noise: float = 0.0, seed: int = 0) -> np.ndarray:
    v = np.zeros(dim, dtype=np.float32)
    v[i] = 1.0
    if noise:
        v += np.random.default_rng(seed).normal(0, noise, dim).astype(np.float32)
    return v / np.linalg.norm(v)


def folder(fid: int, path: str, *, vec: np.ndarray | None = None, profile: np.ndarray | None = None,
           n: int = 3, leaf: bool = True, direct: bool = True, exts: dict[str, int] | None = None,
           tokens: list[str] | None = None) -> FolderIndex:
    return FolderIndex(
        folder_id=fid, path=path, is_leaf=leaf, has_direct_files=direct, centroid=vec,
        profile=profile if profile is not None else np.zeros(8, dtype=np.float32),
        n_files=n, ext_counts=exts or {}, name_tokens=tokens or [],
    )


def test_right_folder_wins_and_softmax_sums_to_one() -> None:
    fs = [folder(1, "A", vec=e(0)), folder(2, "B", vec=e(1)), folder(3, "C", vec=e(2))]
    out = score(e(1, noise=0.05), [], "", fs, [], Scoring())
    assert out[0].folder_id == 2
    assert abs(sum(s.p for s in out) - 1.0) < 1e-9
    assert [s.p for s in out] == sorted((s.p for s in out), reverse=True)
    assert set(out[0].signals) == {"centroid", "profile", "knn", "meta"}


def test_empty_folder_relies_on_profile() -> None:
    fs = [
        folder(1, "Full", vec=e(0), n=5),
        folder(2, "Empty", vec=None, profile=e(3), n=0, direct=False),
    ]
    out = score(e(3), [], "", fs, [], Scoring())
    assert out[0].folder_id == 2
    # missing evidence is neutral: the profile similarity stands in for the centroid
    assert out[0].signals["profile"] > 0.99
    assert out[0].signals["centroid"] == out[0].signals["profile"]
    # profile weight is the empty weight when n_files == 0
    cfg = Scoring()
    assert abs(out[0].raw - (cfg.w_centroid + cfg.w_profile_empty) * 1.0) < 1e-6


def test_knn_vote() -> None:
    fs = [folder(1, "A", vec=None, n=1), folder(2, "B", vec=None, n=1)]
    placed = [PlacedVec(folder_id=1, vec=e(0, noise=0.05, seed=i)) for i in range(3)]
    placed += [PlacedVec(folder_id=2, vec=e(5, noise=0.05, seed=i)) for i in range(3)]
    cfg = Scoring(knn_k=3)
    out = score(e(0), [], "", fs, placed, cfg)
    by = {s.folder_id: s for s in out}
    assert by[1].signals["knn"] > 0.9 and by[2].signals["knn"] < 0.1
    assert out[0].folder_id == 1


def test_meta_ext_and_tokens() -> None:
    fs = [
        folder(1, "Photos", exts={"jpg": 10}, tokens=["img", "holiday"]),
        folder(2, "Docs", exts={"pdf": 10}, tokens=["invoice", "report"]),
    ]
    out = score(np.zeros(8, dtype=np.float32), ["invoice", "march"], ".PDF", fs, [], Scoring())
    assert out[0].folder_id == 2
    assert out[0].signals["meta"] > out[1].signals["meta"]


def test_candidates_leaf_only() -> None:
    root = folder(0, "", leaf=False, direct=False)
    parent = folder(1, "Work", leaf=False, direct=False)
    parent_files = folder(2, "Misc", leaf=False, direct=True)
    leaf = folder(3, "Work/PayPal", leaf=True, direct=False)
    allf = [root, parent, parent_files, leaf]
    assert {f.folder_id for f in candidates(allf, True)} == {2, 3}
    assert {f.folder_id for f in candidates(allf, False)} == {1, 2, 3}
    assert [f.folder_id for f in candidates([root], True)] == [0]
    root_files = folder(0, "", leaf=False, direct=True)
    assert 0 in {f.folder_id for f in candidates([root_files, leaf], True)}


def _sc(*ps: float) -> list[Scored]:
    return [Scored(folder_id=i, path=str(i), raw=0.0, p=p, signals={}) for i, p in enumerate(ps)]


def test_tier_boundaries() -> None:
    t = Tiers()
    assert tier(_sc(0.95, 0.03), t) == ("confirm", 0.95)
    assert tier(_sc(0.90, 0.05), t)[0] == "confirm"
    assert tier(_sc(0.89, 0.05), t)[0] == "choices"
    assert tier(_sc(0.60, 0.3), t)[0] == "choices"
    assert tier(_sc(0.59, 0.3), t)[0] == "inbox"
    assert tier([], t) == ("inbox", 0.0)
    assert tier(_sc(1.0), t)[0] == "confirm"


def test_margin_rule() -> None:
    t = Tiers(confirm=0.5, choices=0.3, min_margin=0.1)
    assert tier(_sc(0.52, 0.48), t)[0] == "choices"  # high conf but margin < 0.1
    assert tier(_sc(0.62, 0.38), t)[0] == "confirm"


def test_build_folder_index_aggregates() -> None:
    specs = [
        FolderSpec(folder_id=1, path=""),
        FolderSpec(folder_id=2, path="Work", parent_id=1),
        FolderSpec(folder_id=3, path="Work/Tax", parent_id=2),
        FolderSpec(folder_id=4, path="Empty", parent_id=1),
    ]
    vecs = {2: [e(0)], 3: [e(0, noise=0.1, seed=1), e(1)]}
    names = {2: ["cv.docx"], 3: ["tax_2023.pdf", "tax_2024.pdf"]}
    profs = {i: e(i) for i in (1, 2, 3, 4)}
    idx = {f.folder_id: f for f in build_folder_index(specs, vecs, names, profs)}
    assert idx[1].n_files == 3 and not idx[1].is_leaf and not idx[1].has_direct_files
    assert idx[2].has_direct_files and idx[2].n_files == 3
    assert idx[3].is_leaf and idx[3].ext_counts == {"pdf": 2}
    assert idx[4].centroid is None and idx[4].n_files == 0
    c = idx[1].centroid
    assert c is not None and abs(float(np.linalg.norm(c)) - 1.0) < 1e-5
    assert "tax" in idx[3].name_tokens and "2024" in idx[3].name_tokens
    assert tokenize("My_Tax-2024.PDF") == ["my", "tax", "2024", "pdf"]


TRAIN = {
    "Invoices": [
        "invoice number 4821 amount due payment terms net 30 bill to customer",
        "invoice total payment due date remit to account balance",
        "tax invoice billing statement amount payable please pay by",
        "invoice for services rendered subtotal vat total due",
        "payment receipt invoice paid amount customer billing address",
    ],
    "Lecture Notes": [
        "lecture notes chapter 3 theorem proof lemma professor said exam",
        "lecture week 5 notes derivative integral calculus examples homework",
        "class notes professor lecture slides topic summary exam review",
        "lecture notes thermodynamics entropy chapter summary course",
        "notes from lecture on algorithms graph theory course syllabus",
    ],
    "Resumes": [
        "resume experience software engineer education skills references",
        "curriculum vitae work experience education skills objective summary",
        "resume professional summary employment history skills certifications",
        "cv engineer experience skills education university degree gpa",
        "resume objective experience projects skills education awards",
    ],
}
HELD_OUT = [
    ("Invoices", "invoice 5530 total amount due payment net 15 customer"),
    ("Invoices", "billing statement payment due invoice balance remit"),
    ("Lecture Notes", "lecture notes chapter 7 proof theorem exam professor"),
    ("Lecture Notes", "course lecture summary notes calculus homework topic"),
    ("Resumes", "resume skills experience education engineer summary"),
    ("Resumes", "curriculum vitae employment history skills education degree"),
]


def test_end_to_end_with_fake_provider() -> None:
    s = Settings()
    s.models.provider = "fake"
    prov = FakeProvider(s)
    names = list(TRAIN)
    specs = [FolderSpec(folder_id=i + 1, path=n) for i, n in enumerate(names)]
    vecs = {i + 1: [np.array(v, dtype=np.float32) for v in prov.embed(TRAIN[n])]
            for i, n in enumerate(names)}
    fnames = {i + 1: [f"doc{j}.pdf" for j in range(5)] for i in range(3)}
    profs = {i + 1: np.array(prov.embed([n])[0], dtype=np.float32) for i, n in enumerate(names)}
    folders = candidates(build_folder_index(specs, vecs, fnames, profs), True)
    placed = [PlacedVec(folder_id=fid, vec=v) for fid, vs in vecs.items() for v in vs]
    correct = 0
    for truth, text in HELD_OUT:
        vec = np.array(prov.embed([text])[0], dtype=np.float32)
        out = score(vec, tokenize(text), "pdf", folders, placed, s.scoring)
        correct += out[0].path == truth
    assert correct >= 5
