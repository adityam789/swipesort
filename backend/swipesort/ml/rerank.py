"""LLM rerank of the top-k folder candidates (SPEC §3). Pure helpers + one IO function."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from swipesort.config import LLM as LLMConfig
from swipesort.config import Tiers
from swipesort.ml.gateway import ModelUnavailable
from swipesort.ml.llm import LLM

REASON_MAX = 120

RERANK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "choice": {"type": "string"},
        "alternates": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "new_folder_suggestion": {"type": ["string", "null"]},
        "reason": {"type": "string", "maxLength": REASON_MAX},
    },
    "required": ["choice", "alternates", "new_folder_suggestion", "reason"],
}


class RerankResult(BaseModel):
    choice: str
    alternates: list[str] = Field(default_factory=list)
    new_folder_suggestion: str | None = None
    reason: str = ""


def build_prompt(file_name: str, digest: str, candidates: list[str], max_chars: int) -> str:
    listing = "\n".join(f"{i}. {c}" for i, c in enumerate(candidates, 1))
    text = digest.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return (
        "You file documents into folders. Pick the best destination folder for this file "
        "from the numbered candidate paths.\n\n"
        f"File name: {file_name}\n"
        f"Content digest:\n{text or '(none)'}\n\n"
        f"Candidate folders:\n{listing}\n\n"
        "Answer with JSON only: "
        '"choice" is exactly one candidate path copied verbatim; '
        '"alternates" is up to 2 other candidate paths, best first; '
        '"new_folder_suggestion" is a better folder path if none fit, otherwise null; '
        f'"reason" is one short sentence (under {REASON_MAX} characters) shown to the user, '
        "stating what this file is and the detail from ITS content that decides the folder "
        "(document type, who it is from or about, and a date or identifier if present). "
        "Use only facts from this file; never invent details."
    )


def _norm(s: str) -> str:
    return s.strip().strip("/\\").strip().casefold()


def _resolve(value: Any, candidates: list[str]) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return candidates[value - 1] if 1 <= value <= len(candidates) else None
    if not isinstance(value, str):
        return None
    v = value.strip()
    if v.isdigit():
        return _resolve(int(v), candidates)
    for c in candidates:
        if _norm(c) == _norm(v):
            return c
    return None


def parse(raw: dict[str, Any], candidates: list[str]) -> RerankResult | None:
    choice = _resolve(raw.get("choice"), candidates)
    if choice is None:
        return None
    alts: list[str] = []
    raw_alts = raw.get("alternates")
    for a in raw_alts if isinstance(raw_alts, list) else []:
        r = _resolve(a, candidates)
        if r is not None and r != choice and r not in alts:
            alts.append(r)
    nf = raw.get("new_folder_suggestion")
    new_folder = nf.strip() if isinstance(nf, str) and nf.strip() else None
    reason = raw.get("reason")
    reason_s = " ".join(reason.split())[:REASON_MAX] if isinstance(reason, str) else ""
    return RerankResult(
        choice=choice, alternates=alts[:2], new_folder_suggestion=new_folder, reason=reason_s
    )


_RANK = {"inbox": 0, "choices": 1, "confirm": 2}


def combine(
    p_top_path: str,
    p_top: float,
    tier: str,
    rr: RerankResult | None,
    cfg_llm: LLMConfig,
    cfg_tiers: Tiers,
) -> tuple[float, str, bool | None]:
    """Merge the LLM verdict with the classifier's top candidate -> (confidence, tier, agrees).

    Agreement boosts confidence and may promote inbox->choices; it never creates a Confirm
    that the classifier (margin rule etc.) had not already granted. Disagreement caps
    confidence below Confirm.
    """
    if rr is None:
        return p_top, tier, None
    if _norm(rr.choice) == _norm(p_top_path):
        conf = min(1.0, p_top + cfg_llm.agree_boost)
        new = "confirm" if conf >= cfg_tiers.confirm else (
            "choices" if conf >= cfg_tiers.choices else "inbox"
        )
        if new == "confirm" and tier != "confirm":
            new = "choices"  # a boost alone never grants Confirm (margin rule lives upstream)
        if _RANK[new] < _RANK.get(tier, 0):
            new = tier
        return conf, new, True
    conf = min(p_top, cfg_llm.disagree_cap, cfg_tiers.confirm - 1e-6)
    return conf, ("choices" if conf >= cfg_tiers.choices else "inbox"), False


def should_rerank(tier: str, cfg_llm: LLMConfig) -> bool:
    tiers = cfg_llm.eco_rerank_tiers if cfg_llm.eco else cfg_llm.rerank_tiers
    return tier in tiers


def rerank(
    llm: LLM, file_name: str, digest: str, candidates: list[str], cfg_llm: LLMConfig
) -> RerankResult | None:
    if not candidates:
        return None
    prompt = build_prompt(file_name, digest, candidates, cfg_llm.max_digest_chars)
    try:
        raw = llm.chat_json(prompt, RERANK_SCHEMA)
    except ModelUnavailable:
        raise
    except Exception:  # noqa: BLE001 - any LLM failure degrades to no rerank
        return None
    return parse(raw, candidates)
