"""Dedup eval on the fixture corpus.

    cd backend && uv run python ../eval/run_dedup_eval.py --n 300 [--hard] [--seed 0]

Runs extraction + features + find_duplicates over the input files only and scores the
matches against the manifest ground truth (dup_of / near_dup_of / version_of).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests" / "fixtures"))

from generate_corpus import generate

from swipesort.config import Settings
from swipesort.engine.dedup import DedupItem, find_duplicates
from swipesort.pipeline.extractors.registry import extract
from swipesort.pipeline.features import compute_features
from swipesort.pipeline.fingerprint import content_hash


class DedupReport(BaseModel):
    n: int
    seed: int
    hard: bool
    seeded: dict[str, int]
    found: dict[str, int]
    exact_recall: float
    near_recall: float
    version_recall: float
    false_exact: int
    matches: int
    correct_matches: int
    precision: float
    wrong: list[str]  # up to 20 examples of incorrect matches
    missed: list[str]  # up to 20 examples of missed seeded pairs
    passed: bool


def _ratio(num: int, den: int) -> float:
    return 1.0 if den == 0 else num / den


def eval_dedup(n: int = 300, seed: int = 0, hard: bool = False) -> DedupReport:
    cfg = Settings()
    with tempfile.TemporaryDirectory(prefix="swipesort-dedup-eval-") as tmp:
        out = Path(tmp) / "corpus"
        manifest = generate(out, n=n, seed=seed, nested=False, hard=hard)
        inp = out / "input"

        items: list[DedupItem] = []
        key_of: dict[str, str] = {}
        rel_of: dict[str, str] = {}
        for e in manifest.entries:  # sorted by rel_path = card order
            p = inp / e.rel_path
            ex = extract(p, cfg.pipeline.max_text_chars, cfg.pipeline.pages)
            feats = compute_features(p, ex.text, ex.is_image, cfg.dedup)
            st = p.stat()
            key = f"f:{e.rel_path}"
            key_of[e.rel_path] = key
            rel_of[key] = e.rel_path
            items.append(
                DedupItem(
                    key=key,
                    side="input",
                    name=p.name,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    ext=p.suffix.lstrip("."),
                    quick_hash=feats.quick_hash,
                    content_hash=content_hash(p),
                    phash=feats.phash,
                    dhash=feats.dhash,
                    width=feats.width,
                    height=feats.height,
                    minhash=feats.minhash,
                    text_len=feats.text_len,
                )
            )
        matches = find_duplicates(items, cfg.dedup)

        # ---- ground-truth groups: original <-> derived, transitively within one original
        parent: dict[str, str] = {e.rel_path: e.rel_path for e in manifest.entries}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        seeded: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for e in manifest.entries:
            for field in ("dup_of", "near_dup_of", "version_of"):
                src = getattr(e, field)
                if src:
                    seeded[field].append((e.rel_path, src))
                    parent[find(e.rel_path)] = find(src)

        def byte_identical(r1: str, r2: str) -> bool:
            return (inp / r1).read_bytes() == (inp / r2).read_bytes()

        # a seeded pair counts as found when the derived file has a qualifying match with any
        # file of the same original's group (e.g. the small copy matched to an exact copy)
        def hit(rel: str, src: str, ok: set[str]) -> bool:
            root = find(rel)
            for m in matches:
                if m.relation not in ok:
                    continue
                ra, rb = rel_of[m.a], rel_of[m.b]
                if rel in (ra, rb) and find(ra) == root and find(rb) == root:
                    return True
            return False

        found = {"dup_of": 0, "near_dup_of": 0, "version_of": 0}
        missed: list[str] = []
        near_ok = {"exact", "near_image", "near_text", "version"}
        ver_ok = {"version", "near_text"}
        for field, ok in (("dup_of", {"exact"}), ("near_dup_of", near_ok), ("version_of", ver_ok)):
            for rel, src in seeded[field]:
                if hit(rel, src, ok):
                    found[field] += 1
                else:
                    other = next((mm for mm in matches if rel_of[mm.a] == rel), None)
                    got = (
                        f"matched {rel_of[other.b]} as {other.relation} {other.similarity:.2f}"
                        if other
                        else "no match"
                    )
                    missed.append(f"{field}: {rel} -> {src} [{got}]")

        false_exact = 0
        correct = 0
        wrong: list[str] = []
        for m in matches:
            ra, rb = rel_of[m.a], rel_of[m.b]
            if m.relation == "exact" and not byte_identical(ra, rb):
                false_exact += 1
            if find(ra) == find(rb) or byte_identical(ra, rb):
                correct += 1
            else:
                wrong.append(f"{m.relation} {m.similarity:.2f}: {ra} ~ {rb}")

    n_seeded = {
        "dup_of": len(seeded["dup_of"]),
        "near_dup_of": len(seeded["near_dup_of"]),
        "version_of": len(seeded["version_of"]),
    }
    exact_recall = _ratio(found["dup_of"], n_seeded["dup_of"])
    near_recall = _ratio(found["near_dup_of"], n_seeded["near_dup_of"])
    version_recall = _ratio(found["version_of"], n_seeded["version_of"])
    return DedupReport(
        n=n,
        seed=seed,
        hard=hard,
        seeded=n_seeded,
        found=found,
        exact_recall=round(exact_recall, 4),
        near_recall=round(near_recall, 4),
        version_recall=round(version_recall, 4),
        false_exact=false_exact,
        matches=len(matches),
        correct_matches=correct,
        precision=round(_ratio(correct, len(matches)), 4),
        wrong=wrong[:20],
        missed=missed[:20],
        passed=exact_recall >= 1.0 and near_recall >= 0.90 and false_exact == 0,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--hard", action="store_true")
    args = ap.parse_args()
    rep = eval_dedup(args.n, args.seed, args.hard)
    print(json.dumps(rep.model_dump(), indent=2))
    print("PASS" if rep.passed else "FAIL")
    sys.exit(0 if rep.passed else 1)


if __name__ == "__main__":
    main()
