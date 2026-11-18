"""Eval on the fixture corpus (SPEC §15): accuracy@1/3, tier distribution, calibration, latency.

    cd backend && uv run python ../eval/run_eval.py --n 300 --provider fake
    cd backend && uv run python ../eval/run_eval.py --n 300 --provider ollama
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlmodel import Session as DB
from sqlmodel import col, select

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests" / "fixtures"))

from generate_corpus import generate

from swipesort.config import Settings
from swipesort.db.models import File, Folder, Suggestion
from swipesort.main import create_app


class EvalReport(BaseModel):
    provider: str
    n: int
    top1: float
    top3: float
    tiers: dict[str, int]
    tier_accuracy: dict[str, float]  # top-1 accuracy within each tier (calibration proxy)
    calibration: list[tuple[float, float, int]]  # (bin center, accuracy, count)
    pipeline_s: float
    # The UI advances optimistically from its prefetched queue (SPEC §2.2): the decide POST runs
    # in the background, and the only request it can wait on is the next-cards refill.
    next_cards_ms_p50: float
    next_cards_ms_p95: float
    decide_ms_p50: float  # background file op + journal; reported, not gated
    decide_ms_p95: float
    misses: list[str]
    # M4 (SPEC §4): where the file would actually go (descent destination)
    path_correct: float = 0.0  # reached the right leaf
    ancestor_inbox: float = 0.0  # stopped mid-tree at an ancestor of the right folder
    dest_wrong: float = 0.0  # wrong leaf, or an inbox outside the right branch
    reranked: int = 0


def run_eval(n: int = 300, seed: int = 0, nested: bool = False, provider: str = "fake",
             workdir: Path | None = None, decide: bool = True,
             settings_patch: dict[str, dict[str, object]] | None = None, hard: bool = False) -> EvalReport:
    tmp = Path(workdir or tempfile.mkdtemp(prefix="swipesort-eval-"))
    manifest = generate(tmp / "corpus", n=n, seed=seed, nested=nested, hard=hard)
    truth = {e.rel_path: e.category for e in manifest.entries}
    anc_only = {e.rel_path: getattr(e, "ancestor_only", None) for e in manifest.entries}
    settings = Settings()
    settings.models.provider = provider
    settings.batch.min_size = settings.batch.inbox_min_size = 10**9  # per-file accuracy
    for section, values in (settings_patch or {}).items():
        for k, v in values.items():
            setattr(getattr(settings, section), k, v)
    app = create_app(db_path=tmp / "eval.db", settings=settings, static_dir=None)
    client = TestClient(app)
    t0 = time.perf_counter()
    sid = client.post("/api/sessions", json={
        "input_root": str(tmp / "corpus" / "input"),
        "output_root": str(tmp / "corpus" / "output")}).json()["id"]
    pipe = app.state.service.pipelines[sid]
    assert pipe.wait(1800), "pipeline timed out"
    if pipe.llm is not None:  # LLM rerank runs after cards are READY; wait for it to drain
        pipe.rerank_idle.wait(3600)
    pipeline_s = time.perf_counter() - t0
    if pipe.error:
        raise SystemExit(f"pipeline error: {pipe.error}")

    engine = app.state.service.engine
    hits1 = hits3 = 0
    d_ok = d_anc = d_bad = 0
    folder_rel: dict[int, str] = {}
    out_root = (tmp / "corpus" / "output").resolve()
    tiers: Counter[str] = Counter()
    tier_hits: Counter[str] = Counter()
    bins: dict[int, list[int]] = {}
    misses: list[str] = []
    with DB(engine) as db:
        files = db.exec(select(File).where(File.session_id == sid)).all()
        for f in files:
            sug = db.exec(select(Suggestion).where(Suggestion.file_id == f.id)
                          .order_by(col(Suggestion.id).desc())).first()
            want = truth[f.rel_path]
            if sug is None:
                misses.append(f"{f.rel_path}: no suggestion")
                tiers["none"] += 1
                continue
            paths = [c["path"] for c in json.loads(sug.candidates_json)]
            if sug.stop_folder_id is not None and sug.stop_folder_id not in folder_rel:
                fo = db.get(Folder, sug.stop_folder_id)
                folder_rel[sug.stop_folder_id] = (
                    "" if fo is None or Path(fo.path) == out_root
                    else Path(fo.path).relative_to(out_root).as_posix())
            anc = anc_only.get(f.rel_path)
            if sug.reached_leaf:
                if paths and paths[0] == want and not anc:
                    d_ok += 1
                elif anc and paths and (paths[0] == anc or paths[0].startswith(anc + "/")):
                    d_anc += 1  # only the ancestor is knowable: any leaf below it is fine
                else:
                    d_bad += 1
            else:
                stop = folder_rel.get(sug.stop_folder_id or -1, "")
                if stop == "" or want == stop or want.startswith(stop + "/"):
                    d_anc += 1
                else:
                    d_bad += 1
            ok1 = bool(paths) and paths[0] == want
            hits1 += ok1
            hits3 += want in paths[:3]
            tiers[sug.tier] += 1
            tier_hits[sug.tier] += ok1
            bins.setdefault(min(int(sug.confidence * 10), 9), []).append(int(ok1))
            if not ok1:
                misses.append(f"{f.rel_path}: want {want}, got {paths[:3]} "
                              f"({sug.tier} {sug.confidence:.2f})")

    lat: list[float] = []
    dlat: list[float] = []
    if decide:  # card -> next card: decide (pick truth) + fetch next ready cards
        folders = {f["path"]: f["id"] for f in
                   client.get(f"/api/sessions/{sid}/folders").json()}
        while True:
            cards = client.get(f"/api/sessions/{sid}/cards", params={"limit": 20}).json()
            if not cards:
                break
            c = cards[0]
            target = folders.get(truth[c["file"]["rel_path"]])
            body = ({"action": "pick", "target_folder_id": target} if target is not None
                    else {"action": "trash"})
            t = time.perf_counter()
            r = client.post(f"/api/cards/{c['id']}/decide", json=body)
            if r.status_code != 200:
                raise RuntimeError(f"decide failed for {c['id']}: {r.text}")
            t2 = time.perf_counter()
            client.get(f"/api/sessions/{sid}/cards", params={"limit": 20})
            lat.append((time.perf_counter() - t2) * 1000)
            dlat.append((t2 - t) * 1000)

    total = len(files) or 1
    def p95(xs: list[float]) -> float:
        return round(statistics.quantiles(xs, n=20)[18], 1) if len(xs) >= 2 else 0.0

    def p50(xs: list[float]) -> float:
        return round(statistics.median(xs), 1) if xs else 0.0
    return EvalReport(
        provider=provider, n=len(files), top1=hits1 / total, top3=hits3 / total,
        tiers=dict(tiers),
        tier_accuracy={t: tier_hits[t] / c for t, c in tiers.items() if c},
        calibration=[((b + 0.5) / 10, sum(v) / len(v), len(v)) for b, v in sorted(bins.items())],
        pipeline_s=round(pipeline_s, 2),
        next_cards_ms_p50=p50(lat), next_cards_ms_p95=p95(lat),
        decide_ms_p50=p50(dlat), decide_ms_p95=p95(dlat),
        misses=misses,
        # correct-path rate over files whose leaf is determinable; for ancestor-only files the
        # right outcome is the ancestor's inbox (counted in ancestor_inbox)
        path_correct=d_ok / max(1, total - sum(1 for v in anc_only.values() if v)),
        ancestor_inbox=d_anc / total, dest_wrong=d_bad / total,
        reranked=pipe.reranked,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--nested", action="store_true")
    ap.add_argument("--provider", default="fake")
    ap.add_argument("--hard", action="store_true", help="use the hard (ambiguous) corpus")
    ap.add_argument("--show-misses", type=int, default=15)
    a = ap.parse_args()
    r = run_eval(a.n, a.seed, a.nested, a.provider, hard=a.hard)
    print(r.model_dump_json(indent=2, exclude={"misses"}))
    for m in r.misses[: a.show_misses]:
        print("  miss:", m)
    ok = r.top1 >= 0.80 and r.top3 >= 0.95 and r.next_cards_ms_p95 < 100
    print("M1 acceptance:", "PASS" if ok else "FAIL")
    if a.nested:
        ok4 = r.path_correct >= 0.75
        print(f"M4: path_correct={r.path_correct:.1%} ancestor_inbox={r.ancestor_inbox:.1%} "
              f"wrong={r.dest_wrong:.1%} ->", "PASS" if ok4 else "FAIL")
        ok = ok and ok4
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
