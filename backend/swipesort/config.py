"""Defaults + user overrides from ~/.swipesort/config.toml (SPEC §16).

All thresholds, weights and model names live here. Never hard-code them elsewhere.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


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


class Dedup(BaseModel):
    image_hamming: int = 6  # pHash AND dHash Hamming distance <= this -> near-dup image
    text_jaccard: float = 0.85  # MinHash Jaccard >= this -> near-dup text
    version_low: float = 0.25  # version-of: Jaccard in [version_low, text_jaccard) + similar stems
    stem_similarity: float = 0.75  # SequenceMatcher ratio of version-normalized stems
    minhash_perm: int = 128
    shingle: int = 5  # word shingles
    min_text_chars: int = 100  # below this, text near-dup / version-of are not attempted


class Proposals(BaseModel):
    min_cluster: int = 3
    cohesion: float = 0.70
    max_sibling_sim: float = 0.55
    singleton_new_topic: float = 0.40


class Watch(BaseModel):
    enabled: bool = False
    folders: list[str] = Field(default_factory=lambda: ["~/Downloads"])
    settle_seconds: float = 5
    auto_move_threshold: float = 0.97
    auto_move: bool = True
    notify: bool = True
    ignore: list[str] = Field(default_factory=lambda: ["*.crdownload", "*.part", "*.download"])


class Trash(BaseModel):
    mode: str = "quarantine"  # quarantine | os
    purge_days: int = 30


class Scoring(BaseModel):
    # raw(F) = w · [s_centroid, s_profile, s_knn, s_meta]  (SPEC §3)
    w_centroid: float = 1.0
    w_profile: float = 0.6
    w_profile_empty: float = 1.5  # profile weight when F has no files (cold start)
    w_knn: float = 1.0
    w_meta: float = 0.6
    temperature: float = 0.08
    knn_k: int = 10
    meta_ext_share: float = 0.5  # s_meta = share*ext_prior + (1-share)*filename BM25
    bm25_k1: float = 1.2
    bm25_b: float = 0.75
    leaf_only: bool = True


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
    host: str = "127.0.0.1"
    port: int = 8765


class Settings(BaseModel):
    tiers: Tiers = Field(default_factory=Tiers)
    scoring: Scoring = Field(default_factory=Scoring)
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


def load_settings(path: Path | None = None) -> Settings:
    path = path or app_dir() / "config.toml"
    if path.is_file():
        with path.open("rb") as f:
            return Settings.model_validate(tomllib.load(f))
    return Settings()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
