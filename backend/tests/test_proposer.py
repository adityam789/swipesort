"""engine/proposer.py and engine/naming.py: pure functions."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from swipesort.config import Proposals
from swipesort.engine import naming, proposer
from swipesort.ml.gateway import ModelUnavailable


def _blobs(centers: int, per: int, dim: int = 32, spread: float = 0.05,
           seed: int = 0) -> tuple[np.ndarray, list[int]]:
    rng = np.random.default_rng(seed)
    base = rng.normal(size=(centers, dim))
    xs, labels = [], []
    for c in range(centers):
        for _ in range(per):
            xs.append(base[c] + rng.normal(scale=spread * np.linalg.norm(base[c]), size=dim))
            labels.append(c)
    return np.asarray(xs, dtype=np.float32), labels


def test_cohesion_and_centroid() -> None:
    x = np.array([[1, 0], [1, 0], [0, 1]], dtype=np.float32)
    assert proposer.cohesion(x[:2]) == pytest.approx(1.0)
    assert proposer.cohesion(x) == pytest.approx(1 / 3)
    assert proposer.cohesion(x[:1]) == 1.0
    c = proposer.centroid(x[:2])
    assert np.linalg.norm(c) == pytest.approx(1.0)
    assert proposer.max_sim(c, [x[2], x[0]]) == pytest.approx(1.0)
    assert proposer.max_sim(c, []) == -1.0


def test_cluster_finds_blobs() -> None:
    x, labels = _blobs(4, 10)
    got = proposer.cluster(x, 3)
    assert set(got.tolist()) - {-1} == {0, 1, 2, 3}
    for c in range(4):  # each blob is one cluster
        assert len({int(got[i]) for i in range(len(labels)) if labels[i] == c} - {-1}) == 1
    assert (proposer.cluster(x[:2], 3) == -1).all()  # too few points: all noise


def test_merge_groups_respects_cohesion() -> None:
    x = np.array([[1, 0, 0], [0.99, 0.1, 0], [0.98, 0, 0.1], [0.97, 0.1, 0.1],
                  [0, 1, 0], [0, 0.99, 0.1]], dtype=np.float32)
    merged = proposer.merge_groups(x, [[0, 1], [2, 3], [4, 5]], 0.9, 0.9)
    assert sorted(map(sorted, merged)) == [[0, 1, 2, 3], [4, 5]]


def test_group_low_confidence_keys_by_stop_node() -> None:
    x, labels = _blobs(2, 6)
    cfg = Proposals(cohesion=0.5)
    items = [proposer.LowConf(file_id=i, key=100 if i < 3 or 6 <= i < 9 else 200)
             for i in range(len(labels))]
    groups, noise = proposer.group_low_confidence(items, x, cfg)
    keys = {g.key for g in groups}
    assert keys == {100, 200}
    for g in groups:  # never mixes stop nodes
        assert all(items[i].key == g.key for i in g.file_ids)
        assert len({labels[i] for i in g.file_ids}) == 1
    assert not noise


def test_decide_group_thresholds() -> None:
    cfg = Proposals()
    assert proposer.decide_group(5, 0.8, 0.3, True, cfg) == "new_folder"
    assert proposer.decide_group(2, 0.8, 0.3, True, cfg) == "inbox"  # too small
    assert proposer.decide_group(5, 0.5, 0.3, True, cfg) == "inbox"  # not cohesive
    assert proposer.decide_group(5, 0.8, 0.6, True, cfg) == "inbox"  # like a sibling
    assert proposer.decide_group(5, 0.8, 0.3, False, cfg) == "inbox"  # no specific name
    assert proposer.is_new_topic(0.2, True, cfg) and not proposer.is_new_topic(0.5, True, cfg)
    assert proposer.overlaps([1, 2, 3], [3, 4], 0.5)
    assert not proposer.overlaps([1, 2, 3], [4], 0.5)


def test_cold_start_taxonomy_two_levels() -> None:
    # 3 "super topics", each with 2-3 subtopics: subtopic blobs sit near their super topic
    rng = np.random.default_rng(1)
    supers = rng.normal(size=(3, 48))
    xs = []
    truth = []
    for s, nsub in enumerate((3, 2, 2)):
        for k in range(nsub):
            sub = supers[s] + rng.normal(scale=0.6, size=48)
            for _ in range(12):
                xs.append(sub + rng.normal(scale=0.1, size=48))
                truth.append((s, k))
    cfg = Proposals(top_min=2, top_max=12, top_merge_sim=0.5, noise_attach=0.5)
    tax = proposer.cold_start_taxonomy(np.asarray(xs, dtype=np.float32), cfg)
    assert len(tax.leaves) == 7
    assert len(tax.tops) == 3
    for top in tax.tops:  # each top folder holds one super topic
        assert len({truth[i][0] for li in top for i in tax.leaves[li]}) == 1
    assert sum(len(lf) for lf in tax.leaves) + len(tax.inbox) == len(xs)


def test_cold_start_top_count_is_clamped() -> None:
    x, _ = _blobs(14, 8, seed=3)
    cfg = Proposals(top_min=5, top_max=12, top_merge_sim=0.99)
    tax = proposer.cold_start_taxonomy(x, cfg)
    assert 5 <= len(tax.tops) <= 12
    assert proposer.cold_start_taxonomy(np.zeros((0, 4), np.float32), cfg).leaves == []


# --------------------------------------------------------------------------- naming

VENDORS = ["acme", "globex", "initech", "hooli", "umbrella", "stark"]
INV = ("name: invoice {v} {i}\nkind: pdf document (.pdf)\ntitle: {v} corp\n"
       "text: INVOICE number {i} amount due payment terms net 30")
REC = ("name: receipt store {i}\nkind: pdf document (.pdf)\n"
       "text: Receipt thank you for shopping subtotal visa")
PHOTO = "name: img {i}\nkind: image photo (.jpg)\ncamera: Canon EOS R6\nsize: 640x480"


def test_fallback_name_is_distinctive_and_plural() -> None:
    cfg = Proposals()
    inv = [INV.format(i=i, v=VENDORS[i]) for i in range(6)]
    rec = [REC.format(i=i) for i in range(6)]
    assert naming.pick_fallback(inv, rec, cfg) == "Invoices"
    assert naming.pick_fallback(rec, inv, cfg) == "Receipts"
    # siblings are unique (case-insensitive): next-best keyword
    alt = naming.pick_fallback(inv, rec, cfg, taken=["invoices"])
    assert alt is not None and alt.casefold() != "invoices"


def test_kind_hint_and_generic_rejection() -> None:
    cfg = Proposals()
    photos = [PHOTO.format(i=i) for i in range(5)]
    assert naming.kind_hint(photos, cfg) == "Photos"
    assert naming.pick_fallback(photos, [INV.format(i=1, v=VENDORS[1])], cfg) == "Photos"
    assert naming.is_generic("Misc", cfg) and naming.is_generic(" new  folder ", cfg)
    assert naming.clean("Other", cfg) is None
    assert naming.clean('Taxes: 2025?', cfg) == "Taxes_ 2025_"
    assert naming.clean("CON", cfg) == "CON_"
    assert naming.clean("x" * 80, cfg) is None
    assert naming.unique("Photos", ["photos", "Photos 2"]) == "Photos 3"
    assert naming.pluralize("invoice") == "invoices" and naming.pluralize("tax") == "tax"
    assert naming.pluralize("library") == "libraries"


def test_name_taxonomy_unique_and_specific() -> None:
    cfg = Proposals()
    docs = [INV.format(i=i, v=VENDORS[i]) for i in range(5)] + [REC.format(i=i) for i in range(5)] + [
        PHOTO.format(i=i) for i in range(5)]
    tops, leaves = naming.name_taxonomy([[0, 1], [2]], [list(range(5)), list(range(5, 10)),
                                                        list(range(10, 15))], docs, cfg)
    assert len(tops) == 2 and tops[1] == "Photos" and leaves[2] == "Photos"
    assert {leaves[0], leaves[1]} == {"Invoices", "Receipts"}
    assert tops[0] not in (leaves[0], leaves[1])
    assert not any(naming.is_generic(n, cfg) for n in tops + leaves)


class FakeLLM:
    def __init__(self, answer: dict[str, Any] | Exception) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def model_id(self) -> str:
        return "fake-llm"

    def chat_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_llm_names_validated() -> None:
    cfg = Proposals()
    samples = [[("a.pdf", "invoice")], [("b.pdf", "receipt")]]
    llm = FakeLLM({"parent": "Finance", "names": ["Invoices", "Misc"]})
    parent, names = naming.llm_names(llm, samples, "", ["Work"], cfg)
    assert parent == "Finance" and names == ["Invoices", None]  # generic rejected
    assert "Group 2" in llm.prompts[0] and "Work" in llm.prompts[0]
    parent, names = naming.llm_names(FakeLLM({"parent": "x", "names": ["Work"]}),
                                     samples[:1], "", ["work"], cfg)
    assert parent is None and names == [None]  # clashes with a sibling
    parent, names = naming.llm_names(FakeLLM(RuntimeError("bad json")), samples, "", [], cfg)
    assert names == [None, None]
    with pytest.raises(ModelUnavailable):
        naming.llm_names(FakeLLM(ModelUnavailable("down")), samples, "", [], cfg)
