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
from swipesort.db.models import File, Suggestion
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
    decide_next_ms_p50: float
    decide_next_ms_p95: float
    misses: list[str]


def run_eval(n: int = 300, seed: int = 0, nested: bool = False, provider: str = "fake",
             workdir: Path | None = None, decide: bool = True, hard: bool = False) -> EvalReport:
    tmp = Path(workdir or tempfile.mkdtemp(prefix="swipesort-eval-"))
    manifest = generate(tmp / "corpus", n=n, seed=seed, nested=nested, hard=hard)
    truth = {e.rel_path: e.category for e in manifest.entries}
    settings = Settings()
    settings.models.provider = provider
    app = create_app(db_path=tmp / "eval.db", settings=settings, static_dir=None)
    client = TestClient(app)
    t0 = time.perf_counter()
    sid = client.post("/api/sessions", json={
        "input_root": str(tmp / "corpus" / "input"),
        "output_root": str(tmp / "corpus" / "output")}).json()["id"]
    pipe = app.state.service.pipelines[sid]
    assert pipe.wait(1800), "pipeline timed out"
    pipeline_s = time.perf_counter() - t0
    if pipe.error:
        raise SystemExit(f"pipeline error: {pipe.error}")

    engine = app.state.service.engine
    hits1 = hits3 = 0
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
            client.post(f"/api/cards/{c['id']}/decide", json=body)
            client.get(f"/api/sessions/{sid}/cards", params={"limit": 20})
            lat.append((time.perf_counter() - t) * 1000)

    total = len(files) or 1
    q = statistics.quantiles(lat, n=20) if len(lat) >= 2 else [0.0] * 19
    return EvalReport(
        provider=provider, n=len(files), top1=hits1 / total, top3=hits3 / total,
        tiers=dict(tiers),
        tier_accuracy={t: tier_hits[t] / c for t, c in tiers.items() if c},
        calibration=[((b + 0.5) / 10, sum(v) / len(v), len(v)) for b, v in sorted(bins.items())],
        pipeline_s=round(pipeline_s, 2),
        decide_next_ms_p50=round(statistics.median(lat), 1) if lat else 0.0,
        decide_next_ms_p95=round(q[18], 1),
        misses=misses,
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
    ok = r.top1 >= 0.80 and r.top3 >= 0.95 and r.decide_next_ms_p95 < 100
    print("M1 acceptance:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
