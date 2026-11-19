"""Folder names for proposals: LLM naming with a deterministic keyword fallback.

The fallback is c-TF-IDF-like: a term scores high when most members contain it and few other
files do. Kind hints (photos, screenshots, code, ...) come from the digest's `kind:` line.
Every name is sanitized for Windows rules and checked against the generic-name list.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from functools import lru_cache
from typing import Any

from swipesort.config import Proposals
from swipesort.exec.names import sanitize_name
from swipesort.ml.gateway import ModelUnavailable
from swipesort.ml.llm import LLM

_TOKEN = re.compile(r"[a-z][a-z0-9'-]*[a-z0-9]|[a-z]{3,}")
# Field lines of a digest (pipeline/digest.py) that describe content worth naming after
_NAME_FIELDS = ("name", "title", "text", "camera", "project type", "subject")
_KIND_FIELD = "kind"
STOPWORDS = frozenset(["a", "about", "above", "after", "again", "all", "also", "am", "an", "and", "any", "are", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more", "most", "my", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "our", "out", "over", "own", "same", "she", "should", "so", "some", "such", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "you", "your", "yours", "via", "per", "using", "used", "use", "new", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "first", "last", "next", "please", "thank", "thanks", "copy", "final", "version", "draft", "img", "image", "jpg", "jpeg", "png", "pdf", "txt", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "csv", "md", "html", "page", "pages", "untitled", "scan", "dsc", "pxl", "mvimg", "week", "weeks", "anonymous", "unspecified", "corpus", "none", "null", "unknown", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "january", "february", "march", "april", "june", "july", "august", "september", "october", "november", "december", "mon", "tue", "wed", "thu", "fri", "sat", "sun", "am", "pm", "today", "date"])


def _fields(digest: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in digest.splitlines():
        key, sep, val = line.partition(":")
        if sep and key.strip().lower() in (*_NAME_FIELDS, _KIND_FIELD) and key == key.lstrip():
            out[key.strip().lower()] = out.get(key.strip().lower(), "") + " " + val
    return out


@lru_cache(maxsize=16384)
def terms(digest: str) -> frozenset[str]:
    f = _fields(digest)
    text = " ".join(f.get(k, "") for k in _NAME_FIELDS).lower()
    return frozenset(t for t in _TOKEN.findall(text) if t not in STOPWORDS and len(t) >= 3
                     and not any(c.isdigit() for c in t))


@lru_cache(maxsize=16384)
def name_terms(digest: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(_fields(digest).get("name", "").lower()))


@lru_cache(maxsize=16384)
def title_terms(digest: str) -> frozenset[str]:
    """Terms of the fields that say what a file *is*: file name, document title, camera."""
    f = _fields(digest)
    text = " ".join(f.get(k, "") for k in ("name", "title", "camera")).lower()
    return frozenset(t for t in _TOKEN.findall(text) if t not in STOPWORDS and len(t) >= 3
                     and not any(c.isdigit() for c in t))


def kind_hints(digests: Sequence[str], cfg: Proposals) -> list[str]:
    """Kind-based names shared by most members, most specific first (config order)."""
    if not digests:
        return []
    kinds = [set(_TOKEN.findall(_fields(d).get(_KIND_FIELD, "").lower())) for d in digests]
    return [label for word, label in cfg.kind_hints.items()
            if sum(1 for k in kinds if word in k) / len(kinds) >= cfg.kind_hint_share]


def kind_hint(digests: Sequence[str], cfg: Proposals) -> str | None:
    hints = kind_hints(digests, cfg)
    return hints[0] if hints else None


def keywords(members: Sequence[str], background: Sequence[str], cfg: Proposals,
             limit: int = 8) -> list[str]:
    """Distinctive terms of `members` vs `background` (digests), best first."""
    if not members:
        return []
    mt = [terms(d) for d in members]
    bt = [terms(d) for d in background]
    df_title: dict[str, int] = {}
    for d in members:  # file name terms count twice: a name says what a file is
        nt = name_terms(d)
        for t in title_terms(d):
            df_title[t] = df_title.get(t, 0) + (2 if t in nt else 1)
    df_in: dict[str, int] = {}
    for ts in mt:
        for t in ts:
            df_in[t] = df_in.get(t, 0) + 1
    df_out: dict[str, int] = {}
    for ts in bt:
        for t in ts:
            if t in df_in:
                df_out[t] = df_out.get(t, 0) + 1
    n_in, n_out = len(mt), max(1, len(bt))
    scored: list[tuple[float, str]] = []
    for t, c in df_in.items():
        p_in = c / n_in
        if p_in < cfg.keyword_min_df and n_in > 1:
            continue
        p_out = df_out.get(t, 0) / n_out
        s = p_in * (p_in - p_out)
        if s > 0:
            s += cfg.keyword_title_bonus * (df_title.get(t, 0) / (2 * n_in)) * (p_in - p_out)
            scored.append((s, t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [t for _, t in scored[:limit]]


def pluralize(word: str) -> str:
    w = word
    if not w.isalpha() or len(w) < 6 or w.endswith("s"):
        return w
    if w.endswith(("x", "ch", "sh", "z")):
        return w + "es"
    if w.endswith("y") and w[-2] not in "aeiou":
        return w[:-1] + "ies"
    return w + "s"


def is_generic(name: str, cfg: Proposals) -> bool:
    n = " ".join(name.replace("_", " ").split()).casefold()
    return not n or n in {g.casefold() for g in cfg.generic_names}


def clean(raw: object, cfg: Proposals) -> str | None:
    """Sanitized folder name or None when empty / generic / too long."""
    if not isinstance(raw, str):
        return None
    s = " ".join(raw.replace("/", " ").replace("\\", " ").split()).strip(" .-_")
    if not s or len(s) > cfg.name_max_len:
        return None
    s = sanitize_name(s)
    if s.startswith(".") or is_generic(s, cfg):
        return None
    return s


def _title(word: str) -> str:
    return word[:1].upper() + word[1:]


def fallback_names(members: Sequence[str], background: Sequence[str], cfg: Proposals,
                   exclude: Iterable[str] = ()) -> list[str]:
    """Candidate names, best first (kind hint, then distinctive keywords)."""
    ex = {e.casefold() for e in exclude}
    hints = kind_hints(members, cfg)
    out: list[str] = list(hints)
    hint = hints[0] if hints else None
    kws = keywords(members, background, cfg)
    in_names = [name_terms(d) for d in members]
    for t in kws:
        share = sum(1 for n in in_names if t in n) / max(1, len(in_names))
        word = _title(pluralize(t) if share >= 0.5 and len(members) > 1 else t)
        out.append(word)
    if hint and kws:  # disambiguates siblings that share a hint (e.g. two photo leaves)
        out += [f"{hint} {_title(t)}" for t in kws[:3]
                if t not in hint.casefold() and pluralize(t) != hint.casefold()]
    if len(kws) >= 2:
        out.append(f"{_title(kws[0])} {_title(kws[1])}")
    seen: set[str] = set()
    names: list[str] = []
    for cand in out:
        c = clean(cand, cfg)
        if c is None or c.casefold() in seen or c.casefold() in ex:
            continue
        seen.add(c.casefold())
        names.append(c)
    return names


def unique(name: str, taken: Iterable[str]) -> str:
    """Case-insensitively unique among `taken` (appends " 2", " 3"...)."""
    t = {x.casefold() for x in taken}
    if name.casefold() not in t:
        return name
    i = 2
    while f"{name} {i}".casefold() in t:
        i += 1
    return f"{name} {i}"


def pick_fallback(members: Sequence[str], background: Sequence[str], cfg: Proposals,
                  taken: Iterable[str] = ()) -> str | None:
    """First non-generic candidate not already taken by a sibling (or the parent)."""
    names = fallback_names(members, background, cfg, exclude=taken)
    if not names:  # loosen: a keyword shared by fewer members still beats a numbered name
        loose = cfg.model_copy(update={"keyword_min_df": cfg.keyword_min_df / 3})
        names = fallback_names(members, background, loose, exclude=taken)
    return names[0] if names else None


# --------------------------------------------------------------------------- LLM naming

NAMES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "parent": {"type": "string"},
        "names": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["parent", "names"],
}

_RULES = (
    "Folder name rules: 1 to 3 words, Title Case, plural nouns where natural, naming the "
    "shared document type or topic of the files (not a single file's details). Never use "
    "vague names such as Misc, Other, Files, Documents, Stuff or New Folder. No dates or "
    "numbers unless every file shares them. No slashes."
)


def _sample_lines(samples: Sequence[tuple[str, str]], max_chars: int = 160) -> str:
    lines = []
    for fname, snippet in samples:
        s = " ".join(snippet.split())
        lines.append(f"  - {fname}: {s[:max_chars]}")
    return "\n".join(lines)


def snippet(digest: str) -> str:
    f = _fields(digest)
    return " ".join(f.get(k, "").strip() for k in ("title", "camera", "text") if f.get(k))


def build_group_prompt(clusters: Sequence[Sequence[tuple[str, str]]], parent_path: str,
                       siblings: Sequence[str]) -> str:
    parts = []
    for i, samples in enumerate(clusters, 1):
        parts.append(f"Group {i}:\n{_sample_lines(samples)}")
    where = f"inside the folder '{parent_path}/'" if parent_path else "at the top level"
    sib = (f" Existing folders there (names must differ): {', '.join(siblings)}."
           if siblings else "")
    multi = len(clusters) > 1
    return (
        "You organize a person's files into folders. Each group below is a set of similar "
        "files (sample file names with a content snippet).\n\n"
        + "\n\n".join(parts) + "\n\n"
        + (f"The groups become sibling subfolders of one new parent folder, created {where}."
           if multi else f"The group becomes one new folder, created {where}.")
        + sib + "\n" + _RULES + "\n"
        + "Answer with JSON only: "
        + ('"parent" is the name of the parent folder covering all groups; '
           if multi else '"parent" is an empty string; ')
        + f'"names" is a list of exactly {len(clusters)} folder name(s), one per group in '
        "order, all different."
    )


def llm_names(llm: LLM, clusters: Sequence[Sequence[tuple[str, str]]], parent_path: str,
              siblings: Sequence[str], cfg: Proposals) -> tuple[str | None, list[str | None]]:
    """(parent name or None, one name or None per cluster). Invalid/generic names -> None."""
    prompt = build_group_prompt(clusters, parent_path, siblings)
    try:
        raw = llm.chat_json(prompt, NAMES_SCHEMA)
    except ModelUnavailable:
        raise
    except Exception:  # noqa: BLE001 - any LLM failure degrades to the fallback names
        return None, [None] * len(clusters)
    names = raw.get("names")
    lst = names if isinstance(names, list) else []
    out: list[str | None] = []
    seen = {s.casefold() for s in siblings}
    for i in range(len(clusters)):
        c = clean(lst[i], cfg) if i < len(lst) else None
        if c is not None and c.casefold() in seen:
            c = None
        if c is not None:
            seen.add(c.casefold())
        out.append(c)
    parent = clean(raw.get("parent"), cfg) if len(clusters) > 1 else None
    if parent is not None and parent.casefold() in {s.casefold() for s in siblings}:
        parent = None
    return parent, out


def name_taxonomy(tops: Sequence[Sequence[int]], leaves: Sequence[Sequence[int]],
                  digests: Sequence[str], cfg: Proposals) -> tuple[list[str], list[str]]:
    """Fallback names for a cold-start taxonomy: (one per top, one per leaf). A top with a
    single leaf is one folder: its leaf name equals the top name. Leaves are named first so
    a parent never takes its child's best name."""
    top_names: list[str] = []
    leaf_names: list[str] = [""] * len(leaves)

    def bg(ids: set[int]) -> list[str]:
        return [d for i, d in enumerate(digests) if i not in ids]

    for ti, top in enumerate(tops):
        members = sorted({i for li in top for i in leaves[li]})
        if len(top) == 1:
            name = pick_fallback([digests[i] for i in members], bg(set(members)), cfg,
                                 taken=top_names)
            top_name = unique(name or f"Group {ti + 1}", top_names)
            top_names.append(top_name)
            leaf_names[top[0]] = top_name
            continue
        sib: list[str] = []
        for li in top:
            lset = set(leaves[li])
            nm = pick_fallback([digests[i] for i in leaves[li]], bg(lset), cfg, taken=sib)
            nm = unique(nm or f"Group {ti + 1}.{len(sib) + 1}", sib)
            sib.append(nm)
            leaf_names[li] = nm
        name = pick_fallback([digests[i] for i in members], bg(set(members)), cfg,
                             taken=[*top_names, *sib])
        if name is None:
            joined = f"{sib[0]} & {sib[1]}"
            name = joined if len(joined) <= cfg.name_max_len else f"Group {ti + 1}"
        top_names.append(unique(name, [*top_names, *sib]))
    return top_names, leaf_names
