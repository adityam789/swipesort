"""M5 eval (SPEC §6.2, §14): cold start on an empty output tree -> one "Proposed structure".

    cd backend && uv run python ../eval/run_cold_eval.py --n 300 --provider fake
    cd backend && uv run python ../eval/run_cold_eval.py --n 300 --provider local --llm ollama

Reports: number of top folders, depth, leaf purity, rediscovery of the corpus's hidden
categories (a proposed node >= 70% pure covering >= 60% of the category), generic names,
the proposed tree, and (after accepting it) how files score against the new folders.
"""

from __future__ import annotations

import argparse
import json
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
from swipesort.db.models import File, Proposal, Suggestion
from swipesort.engine.naming import is_generic
from swipesort.main import create_app

PURE = 0.70  # a node "is" a category when >= 70% of its files belong to it
COVER = 0.60  # ... and it holds >= 60% of that category's files
MIN_CAT = 5  # categories with fewer input files are not scored


class NodeReport(BaseModel):
    path: str
    count: int
    majority: str
    purity: float
    children: list[NodeReport] = []


class ColdReport(BaseModel):
    provider: str
    llm: str
    n: int
    named_by: str
    n_top: int
    depth: int
    n_leaves: int
    purity: float  # leaf purity, weighted by size
    rediscovery: float  # share of categories (>= MIN_CAT files) rediscovered
    rediscovered: dict[str, str]  # category -> node path ("" = missed)
    generic_names: list[str]
    inbox: int
    taxonomy_s: float  # session start -> taxonomy card ready (fallback names)
    naming_s: float  # extra time for LLM names
    tree: list[NodeReport]
    finance_together: bool  # invoices, receipts and taxes share a top folder ("sensible")
    # after accepting the whole structure: files are scored against the new folders
    after_same_leaf: float = 0.0  # top suggestion = the leaf the file was clustered into
    after_category_ok: float = 0.0  # top suggestion's majority category = file's category
    after_tiers: dict[str, int] = {}


def _nodes(tax: dict[str, object]) -> list[dict[str, object]]:
    return list(tax["nodes"])  # type: ignore[call-overload]


def run_cold_eval(n: int = 300, seed: int = 0, provider: str = "fake", llm: str = "none",
                  workdir: Path | None = None, accept: bool = True,
                  settings_patch: dict[str, dict[str, object]] | None = None) -> ColdReport:
    tmp = Path(workdir or tempfile.mkdtemp(prefix="swipesort-cold-"))
    manifest = generate(tmp / "corpus", n=n, seed=seed, cold=True)
    truth = {e.rel_path: e.category for e in manifest.entries}
    settings = Settings()
    settings.models.provider = provider
    settings.llm.provider = llm
    # naming only: no per-file rerank in this eval (it doesn't affect the structure)
    settings.llm.rerank_tiers, settings.llm.eco_rerank_tiers = [], []
    settings.llm.resolve_inbox = False
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
    if pipe.error:
        raise SystemExit(f"pipeline error: {pipe.error}")
    t_tax = time.perf_counter() - t0
    assert pipe.proposals_idle.wait(1800), "naming timed out"
    t_named = time.perf_counter() - t0
    engine = app.state.service.engine
    with DB(engine) as db:
        files = {f.id: f for f in db.exec(select(File).where(File.session_id == sid)).all()}
        pr = db.exec(select(Proposal).where(Proposal.session_id == sid,
                                            Proposal.kind == "taxonomy")).first()
        if pr is None:
            raise SystemExit("no taxonomy proposal")
        tax = json.loads(pr.taxonomy_json)
        named_by = pr.named_by
        pid = pr.id
    cat = {fid: truth[f.rel_path] for fid, f in files.items()}
    cat_size = Counter(cat.values())

    def members(node: dict[str, object]) -> list[int]:
        own = list(node["members"])  # type: ignore[call-overload]
        return own + [i for c in node["children"] for i in members(c)]  # type: ignore[attr-defined]

    def report(node: dict[str, object], prefix: str) -> NodeReport:
        mem = members(node)
        cnt = Counter(cat[i] for i in mem)
        maj, k = cnt.most_common(1)[0] if cnt else ("", 0)
        path = f"{prefix}{node['name']}"
        return NodeReport(path=path, count=len(mem), majority=maj,
                          purity=k / max(1, len(mem)),
                          children=[report(c, path + "/") for c in node["children"]])  # type: ignore[attr-defined]

    tree = [report(nd, "") for nd in _nodes(tax)]
    all_nodes: list[tuple[str, list[int]]] = []
    leaves: list[tuple[str, list[int]]] = []
    for nd in _nodes(tax):
        all_nodes.append((str(nd["name"]), members(nd)))
        kids = nd["children"]
        if not kids:
            leaves.append((str(nd["name"]), members(nd)))
        for c in kids:  # type: ignore[attr-defined]
            p = f"{nd['name']}/{c['name']}"
            all_nodes.append((p, members(c)))
            leaves.append((p, members(c)))
    leaf_of = {i: p for p, mem in leaves for i in mem}
    pure_n = sum(Counter(cat[i] for i in mem).most_common(1)[0][1] for _, mem in leaves if mem)
    purity = pure_n / max(1, sum(len(m) for _, m in leaves))
    rediscovered: dict[str, str] = {}
    for c, size in sorted(cat_size.items()):
        if size < MIN_CAT:
            continue
        hit = ""
        for p, mem in all_nodes:
            inside = sum(1 for i in mem if cat[i] == c)
            if mem and inside / len(mem) >= PURE and inside / size >= COVER:
                hit = p
                break
        rediscovered[c] = hit
    names = [p.rsplit("/", 1)[-1] for p, _ in all_nodes]
    generic = [nm for nm in names if is_generic(nm, settings.proposals)
               or nm.lower().startswith(("group ", "cluster "))]
    fin = {"Finance/Invoices", "Finance/Receipts", "Finance/Taxes/2025"}
    fin_tops = {t.path for t in tree for leaf in (t.children or [t]) if leaf.majority in fin}
    rep = ColdReport(
        provider=provider, llm=llm, n=len(files), named_by=named_by, n_top=len(tree),
        depth=2 if any(t.children for t in tree) else 1, n_leaves=len(leaves),
        purity=purity,
        rediscovery=sum(1 for v in rediscovered.values() if v) / max(1, len(rediscovered)),
        rediscovered=rediscovered, generic_names=generic, inbox=len(tax.get("inbox", [])),
        taxonomy_s=round(t_tax, 1), naming_s=round(t_named - t_tax, 1), tree=tree,
        finance_together=len(fin_tops) == 1,
    )
    if accept:
        r = client.post(f"/api/proposals/{pid}/decide", json={"action": "accept"})
        if r.status_code != 200:
            raise RuntimeError(r.text)
        pipe.rescore_stale()
        majority_of = {p: Counter(cat[i] for i in mem).most_common(1)[0][0]
                       for p, mem in leaves if mem}
        same = ok = 0
        tiers: Counter[str] = Counter()
        with DB(engine) as db:
            for fid in files:
                sg = db.exec(select(Suggestion).where(
                    Suggestion.file_id == fid, Suggestion.kind == "classify")
                    .order_by(col(Suggestion.id).desc())).first()
                if sg is None:
                    continue
                cands = json.loads(sg.candidates_json)
                top = cands[0]["path"] if cands else ""
                tiers[sg.tier] += 1
                same += top == leaf_of.get(fid)
                ok += majority_of.get(top) == cat[fid]
        rep.after_same_leaf = same / max(1, len(files))
        rep.after_category_ok = ok / max(1, len(files))
        rep.after_tiers = dict(tiers)
    pipe.stop()
    return rep


def print_tree(nodes: list[NodeReport], indent: str = "  ") -> None:
    for nd in nodes:
        name = nd.path.rsplit("/", 1)[-1]
        print(f"{indent}{name}/  ({nd.count} files, {nd.purity:.0%} {nd.majority})")
        print_tree(nd.children, indent + "    ")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--provider", default="fake")
    ap.add_argument("--llm", default="none", help="none | ollama | llamacpp (folder naming)")
    ap.add_argument("--no-accept", action="store_true")
    a = ap.parse_args()
    r = run_cold_eval(a.n, a.seed, a.provider, a.llm, accept=not a.no_accept)
    print(r.model_dump_json(indent=2, exclude={"tree"}))
    print("Proposed structure:")
    print_tree(r.tree)
    ok = (5 <= r.n_top <= 12 and r.depth <= 2 and r.rediscovery >= 0.75 and r.purity >= 0.8
          and not r.generic_names)
    print(f"M5: top={r.n_top} purity={r.purity:.1%} rediscovery={r.rediscovery:.0%} "
          f"generic={r.generic_names} finance_together={r.finance_together} ->",
          "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
