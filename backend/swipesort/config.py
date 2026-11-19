"""Defaults + user overrides from ~/.swipesort/config.toml.

All thresholds, weights and model names live here. Never hard-code them elsewhere.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class Tiers(BaseModel):
    confirm: float = 0.90
    choices: float = 0.60
    descend: float = 0.75
    min_margin: float = 0.10


class Pipeline(BaseModel):
    lookahead: int = 15
    embed_batch: int = 32
    max_text_chars: int = 4000
    pages: int = 2
    extract_workers: int = 0  # 0 = os.cpu_count()
    urgent_batch: int = 8  # embed batch size while the READY buffer is below lookahead+batch
    index_bump_every: int = 25  # moves into one folder before index_version bumps


class Dedup(BaseModel):
    image_hamming: int = 6  # pHash AND dHash Hamming distance <= this -> near-dup image
    text_jaccard: float = 0.85  # MinHash Jaccard >= this -> near-dup text
    version_low: float = 0.25  # version-of: Jaccard in [version_low, text_jaccard) + similar stems
    stem_similarity: float = 0.75  # SequenceMatcher ratio of version-normalized stems
    minhash_perm: int = 128
    shingle: int = 5  # word shingles
    min_text_chars: int = 100  # below this, text near-dup / version-of are not attempted


class Proposals(BaseModel):
    """New folders & cold start. Similarities are cosines on the embedding model's
    scale, so `by_provider` overrides them per provider (like scoring.temperature_by_provider)."""

    enabled: bool = True
    # low-confidence routing: one card per group of < tiers.choices files
    min_cluster: int = 3  # HDBSCAN min_cluster_size; also the minimum live members of a card
    cohesion: float = 0.70  # mean pairwise cosine of the group
    max_sibling_sim: float = 0.55  # group centroid vs every sibling folder (centroid or profile)
    singleton_new_topic: float = 0.40  # a lone file this far from every folder -> new-topic card
    group_merge_sim: float = 0.85  # merge over-split groups whose centroids are this close
    singleton_max: int = 3  # at most this many singleton proposals per run
    dismissed_overlap: float = 0.5  # don't re-propose a group sharing this much with a dismissal
    # re-run triggers
    every_decisions: int = 25
    inbox_trigger: int = 8  # files sent to _Inbox folders since the last run
    # cold start (empty output): leaf clusters -> 5..12 top folders, 2 levels max
    cold_min_cluster_frac: float = 0.02  # leaf min_cluster_size = max(min_cluster, frac * n)
    cold_min_samples: int = 0  # HDBSCAN min_samples for the leaf pass (0 = library default)
    top_min: int = 5
    top_max: int = 12
    top_merge_sim: float = 0.75  # leaf centroids at least this similar share a top folder
    noise_attach: float = 0.70  # unclustered file joins its nearest leaf at >= this cosine
    # accepted folders start with this mix of member centroid and name embedding as profile
    seed_centroid_weight: float = 0.8
    # naming
    llm_naming: bool = True
    llm_samples: int = 6  # member samples per cluster shown to the LLM
    name_max_len: int = 40
    keyword_min_df: float = 0.3  # a fallback keyword must occur in this share of members
    keyword_title_bonus: float = 1.0  # extra weight for terms in file names / titles / camera
    kind_hint_share: float = 0.7  # share of members whose digest kind matches a hint
    kind_hints: dict[str, str] = Field(default_factory=lambda: {
        "screenshot": "Screenshots", "photo": "Photos", "image": "Images", "code": "Code",
        "video": "Videos", "audio": "Audio", "spreadsheet": "Spreadsheets",
    })
    generic_names: list[str] = Field(default_factory=lambda: [
        "misc", "miscellaneous", "other", "others", "stuff", "files", "file", "documents",
        "document", "docs", "untitled", "new folder", "folder", "unsorted", "various", "general",
        "items", "things", "data", "inbox", "_inbox", "archive", "temp", "tmp", "downloads",
        "desktop", "random", "assorted",
    ])
    by_provider: dict[str, dict[str, float]] = Field(default_factory=lambda: {
        # hashing embeddings: unrelated texts sit near 0, same-kind texts around 0.4-0.7
        "fake": {"cohesion": 0.35, "max_sibling_sim": 0.55, "singleton_new_topic": 0.15,
                 "top_merge_sim": 0.30, "noise_attach": 0.35, "group_merge_sim": 0.30},
        # bge-small: compressed band (unrelated ~0.5, same topic ~0.8)
        "local": {"cohesion": 0.72, "max_sibling_sim": 0.85, "singleton_new_topic": 0.55,
                  "top_merge_sim": 0.75, "noise_attach": 0.70, "group_merge_sim": 0.85},
    })


class WatchFolder(BaseModel):
    """One watched folder and the output tree its files are sorted into."""

    path: str
    output: str = ""  # empty: watch.default_output
    enabled: bool = True


class Watch(BaseModel):
    """Watch mode. `folders` accepts plain strings (backward compatible: they use
    `default_output`) or {path, output, enabled} tables."""

    enabled: bool = False
    folders: list[WatchFolder] = Field(default_factory=lambda: [WatchFolder(path="~/Downloads")])
    default_output: str = ""  # output tree for plain-string folder entries
    settle_seconds: float = 5  # size + mtime stable this long (and not locked) -> process
    settle_poll_seconds: float = 0.5  # how often the settler re-checks tracked paths
    rescan_seconds: float = 30  # periodic top-level rescan for events the OS dropped
    auto_move_threshold: float = 0.97
    auto_move: bool = True
    # auto actions stay off until the user made this many decisions (counted
    # across all sessions: calibration is global, not per folder)
    min_decisions_for_auto: int = 50
    auto_trash_exact_dupes: bool = False  # exact dupes of an output-tree file -> quarantine
    notify: bool = True
    notify_summary_over: int = 3  # more auto-moves than this in one burst -> one summary toast
    digest_days: int = 7  # the activity digest ("what was auto-sorted this week")
    ignore: list[str] = Field(default_factory=lambda: [
        "*.crdownload", "*.part", "*.download", "*.tmp", "*.partial", "*.opdownload",
        "~$*", ".~lock.*",
    ])

    @field_validator("folders", mode="before")
    @classmethod
    def _plain_paths(cls, v: object) -> object:
        if isinstance(v, list):
            return [{"path": x} if isinstance(x, str) else x for x in v]
        return v

    def resolved_folders(self) -> list[WatchFolder]:
        """Folders with `output` filled from `default_output`; entries without one are dropped."""
        out: list[WatchFolder] = []
        for f in self.folders:
            output = f.output or self.default_output
            if output:
                out.append(f.model_copy(update={"output": output}))
        return out


class Trash(BaseModel):
    mode: str = "quarantine"  # quarantine | os
    purge_days: int = 30


class Scoring(BaseModel):
    # raw(F) = w · [s_centroid, s_profile, s_knn, s_meta]
    w_centroid: float = 1.0
    w_profile: float = 0.6
    w_profile_empty: float = 1.5  # profile weight when F has no files (cold start)
    w_knn: float = 1.0
    w_meta: float = 0.6
    temperature: float = 0.08
    # Softmax temperature depends on the embedding model's similarity spread (bge cosines sit
    # in a compressed band). Tuned on the eval corpora: Confirm accuracy >= 95%.
    temperature_by_provider: dict[str, float] = Field(
        default_factory=lambda: {"local": 0.045, "ollama": 0.045})
    knn_k: int = 10
    meta_ext_share: float = 0.5  # s_meta = share*ext_prior + (1-share)*filename BM25
    bm25_k1: float = 1.2
    bm25_b: float = 0.75
    leaf_only: bool = True
    # "none of these" option (see scoring.estimate_null_raw): quantile of how well existing
    # files score against folders they don't belong to; offset shifts it (higher = stricter)
    use_null: bool = True
    null_quantile: float = 0.25  # tuned on the hard corpus (eval sweep): low = only no-fit files
    null_offset: float = 0.0
    null_min_samples: int = 1


class Descent(BaseModel):
    path_conf: str = "product"  # product | min
    method: str = "mass"  # mass (flat leaf probability mass) | centroid


class Batch(BaseModel):
    min_size: int = 3  # "14 files -> Finance/Taxes/2025 at >= 95%, confirm all?"
    threshold: float = 0.95
    inbox_min_size: int = 3  # low-confidence files at the same stop node -> one inbox card


class LLM(BaseModel):
    provider: str = "ollama"  # ollama | llamacpp | none
    # llama.cpp in-process (optional extra `llm-inprocess`): GGUF pulled from Hugging Face
    llamacpp_repo: str = "Qwen/Qwen2.5-3B-Instruct-GGUF"
    llamacpp_file: str = "qwen2.5-3b-instruct-q4_k_m.gguf"
    llamacpp_ctx: int = 4096
    rerank_top_k: int = 5
    rerank_tiers: list[str] = Field(default_factory=lambda: ["confirm", "choices"])
    eco_rerank_tiers: list[str] = Field(default_factory=lambda: ["choices"])
    eco: bool = False  # smaller model + rerank only Choices (CPU-only machines)
    eco_model: str = "qwen2.5:3b-instruct"
    agree_boost: float = 0.05
    disagree_cap: float = 0.89  # must stay below tiers.confirm
    max_digest_chars: int = 1200
    resolve_inbox: bool = True  # LLM picks among siblings when descent stops mid-tree
    timeout_s: float = 60


class Models(BaseModel):
    provider: str = "local"  # local (fastembed + Ollama for LLM) | ollama | fake
    local_embed: str = "BAAI/bge-small-en-v1.5"
    ollama_url: str = "http://127.0.0.1:11434"
    fake_dim: int = 512
    request_timeout_s: float = 60
    embed: str = "nomic-embed-text"
    vlm: str = "moondream"
    llm: str = "qwen2.5:7b-instruct"
    allow_cloud: bool = False


class Scan(BaseModel):
    skip_hidden: bool = True
    follow_symlinks: bool = False
    skip_cloud_placeholders: bool = True
    skip_names: list[str] = Field(
        default_factory=lambda: ["desktop.ini", "Thumbs.db", ".DS_Store", "Icon\r"]
    )
    bundle_markers: list[str] = Field(
        default_factory=lambda: [
            ".git", "package.json", "pyproject.toml", "Cargo.toml", "go.mod", ".hg", ".svn",
        ]
    )
    bundle_suffixes: list[str] = Field(
        default_factory=lambda: [
            ".app", ".xcodeproj", ".xcworkspace", ".photoslibrary", ".bundle", ".framework",
            ".pkg", ".rtfd", ".pages", ".numbers", ".key", ".logicx", ".fcpbundle",
        ]
    )


class Executor(BaseModel):
    locked_retries: int = 3
    locked_backoff_s: float = 0.2


class Server(BaseModel):
    host: str = "127.0.0.1"  # localhost-only mode (default): no firewall prompt, no phone
    port: int = 8765
    # LAN mode (`swipesort serve --lan`): bind lan_host so a paired phone can connect.
    # The first bind to 0.0.0.0 makes Windows Firewall ask for permission.
    lan: bool = False
    lan_host: str = "0.0.0.0"
    print_qr: bool = True  # print a pairing QR in the terminal when the LAN daemon starts
    # Host headers accepted on trusted loopback requests (DNS-rebinding guard; any port).
    # "testserver" is what Starlette's TestClient sends.
    local_hostnames: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "[::1]", "::1", "testserver"])
    # pairing
    pair_code_ttl_s: int = 300
    pair_code_length: int = 8
    pair_max_failures: int = 5  # failed redeems per client IP within the window -> 429
    pair_global_max_failures: int = 20  # all clients; exceeding it voids every pending code
    pair_failure_window_s: int = 300
    token_ttl_days: int = 180  # device tokens; 0 = never expire (still revocable)
    token_cookie: str = "swipesort_token"
    last_seen_every_s: int = 60  # throttle for device.last_seen writes
    # previews served to phones: large images are downscaled (cached) before sending
    preview_max_px: int = 1600
    preview_downscale_bytes: int = 1_500_000
    preview_jpeg_quality: int = 82


class Settings(BaseModel):
    tiers: Tiers = Field(default_factory=Tiers)
    scoring: Scoring = Field(default_factory=Scoring)
    descent: Descent = Field(default_factory=Descent)
    batch: Batch = Field(default_factory=Batch)
    llm: LLM = Field(default_factory=LLM)
    pipeline: Pipeline = Field(default_factory=Pipeline)
    dedup: Dedup = Field(default_factory=Dedup)
    proposals: Proposals = Field(default_factory=Proposals)
    watch: Watch = Field(default_factory=Watch)
    trash: Trash = Field(default_factory=Trash)
    models: Models = Field(default_factory=Models)
    scan: Scan = Field(default_factory=Scan)
    executor: Executor = Field(default_factory=Executor)
    server: Server = Field(default_factory=Server)


def app_dir() -> Path:
    return Path(os.environ.get("SWIPESORT_HOME", Path.home() / ".swipesort"))


def watch_overlay_path(home: Path | None = None) -> Path:
    """Watch settings changed in the app (PUT /api/watch) are saved here, over config.toml."""
    return (home or app_dir()) / "watch.json"


def load_settings(path: Path | None = None) -> Settings:
    path = path or app_dir() / "config.toml"
    s = Settings()
    if path.is_file():
        with path.open("rb") as f:
            s = Settings.model_validate(tomllib.load(f))
    overlay = watch_overlay_path(path.parent)
    if overlay.is_file():  # runtime watch settings saved by PUT /api/watch
        try:
            data = json.loads(overlay.read_text(encoding="utf-8"))
            s.watch = Watch.model_validate({**s.watch.model_dump(), **data})
        except (OSError, ValueError):
            pass  # a broken overlay never stops the daemon; config.toml still applies
    if os.environ.get("SWIPESORT_WATCH") == "1":  # `swipesort serve --watch`
        s.watch.enabled = True
    # `swipesort serve --lan/--port` pass these down (also to uvicorn's reload subprocess).
    if os.environ.get("SWIPESORT_LAN") is not None:
        s.server.lan = os.environ["SWIPESORT_LAN"] == "1"
    if os.environ.get("SWIPESORT_PORT"):
        s.server.port = int(os.environ["SWIPESORT_PORT"])
    return s


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
