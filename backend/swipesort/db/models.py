"""SQLite data model (SPEC §11). M0 subset; later milestones add columns/tables."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(UTC)


class FileStatus(StrEnum):
    queued = "queued"
    extracted = "extracted"
    embedded = "embedded"
    ready = "ready"
    decided = "decided"
    skipped = "skipped"
    error = "error"


class OpType(StrEnum):
    move = "move"
    mkdir = "mkdir"
    trash = "trash"
    rmdir = "rmdir"


class OpStatus(StrEnum):
    intent = "intent"
    done = "done"
    undone = "undone"
    failed = "failed"


class Session(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    input_root: str
    output_root: str
    created_at: datetime = Field(default_factory=now)
    status: str = "active"
    config_json: str = "{}"


class File(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    path: str  # absolute, current location
    rel_path: str  # relative to input_root at scan time (posix separators)
    is_bundle: bool = False
    size: int = 0
    mtime: float = 0.0
    mime: str = ""
    ext: str = ""
    quick_hash: str = ""
    content_hash: str = ""
    status: FileStatus = FileStatus.queued


class Folder(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    path: str  # absolute
    parent_id: int | None = Field(default=None, foreign_key="folder.id")
    depth: int = 0
    is_leaf: bool = True
    is_inbox: bool = False
    description: str = ""
    n_files: int = 0
    created_by: str = "existing"  # existing|user|proposal|inbox


class Decision(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    file_id: int | None = Field(default=None, foreign_key="file.id")
    suggestion_id: int | None = None
    action: str  # accept|choose_1..3|pick|inbox|trash|keep_both|keep_this|defer|mkdir
    target_folder_id: int | None = None
    ts: datetime = Field(default_factory=now)
    latency_ms: int = 0
    undone: bool = False
    payload_json: str = ""  # batch decisions: {"members": [...], "tiers": {...}} for undo


class Op(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    decision_id: int | None = Field(default=None, foreign_key="decision.id")
    type: OpType
    src: str = ""
    dst: str = ""
    status: OpStatus = OpStatus.intent
    ts: datetime = Field(default_factory=now)
    error: str = ""


class Extraction(SQLModel, table=True):
    """Stage output cached by content hash (SPEC §7 caching)."""

    content_hash: str = Field(primary_key=True)
    mime: str = ""
    digest_text: str = ""
    metadata_json: str = "{}"
    page_count: int = 0
    ocr_used: bool = False


class Embedding(SQLModel, table=True):
    """Digest embeddings, keyed by (content_hash|text-key, model). vec = float32 little-endian."""

    key: str = Field(primary_key=True)  # content_hash, or "folder-profile:<sha1 of text>"
    model: str = Field(primary_key=True)
    dim: int
    vec: bytes


class IndexedFile(SQLModel, table=True):
    """A file currently living in the output tree (pre-existing or placed this session)."""

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    folder_id: int = Field(foreign_key="folder.id", index=True)
    path: str
    name: str
    ext: str = ""
    content_hash: str = ""
    file_id: int | None = None  # set when it was placed by a decision this session


class Suggestion(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    file_id: int = Field(foreign_key="file.id", index=True)
    kind: str = "classify"  # classify|duplicate|batch|folder_proposal
    candidates_json: str = "[]"  # [{folder_id, path, p, signals:{...}}] sorted desc
    confidence: float = 0.0
    tier: str = "inbox"  # confirm|choices|inbox
    reason: str = ""
    tree_version: int = 0
    index_version: int = 0
    model_version: str = ""
    created_at: datetime = Field(default_factory=now)
    stale: bool = False
    # M4: confidence-gated descent + LLM rerank
    stop_folder_id: int | None = None  # descent stop node (inbox goes to <stop>/_Inbox)
    path_conf: float = 0.0
    reached_leaf: bool = True
    llm_json: str = ""  # raw rerank output {choice, alternates, new_folder_suggestion, reason}
    llm_agrees: bool | None = None
    batch_ok: bool = True  # False after "review"/"reject_all": shown as an individual card


class Features(SQLModel, table=True):
    """Dedup features cached by content hash (SPEC §5)."""

    content_hash: str = Field(primary_key=True)
    quick_hash: str = ""
    phash: str = ""  # hex, images only
    dhash: str = ""
    width: int = 0
    height: int = 0
    minhash: bytes = b""  # datasketch LeanMinHash hashvalues as uint64 LE, text docs only
    text_len: int = 0


class Proposal(SQLModel, table=True):
    """A new-folder proposal (SPEC §6, §11).

    kind: folder (one new folder under `parent_folder_id`, the descent stop node) |
          taxonomy (cold start: a 2-level structure under the output root, see taxonomy_json).
    status: pending | accepted | inboxed | dismissed | withdrawn
    Members decided individually simply drop out (computed live); a card with fewer than
    min(proposals.min_cluster, original size) live members is not shown.
    """

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    parent_folder_id: int | None = Field(default=None, foreign_key="folder.id")
    name: str = ""
    member_file_ids_json: str = "[]"
    cohesion: float = 0.0
    status: str = "pending"
    kind: str = "folder"
    # {"nodes": [{"id", "name", "members": [...], "children": [...]}], "inbox": [...]}
    taxonomy_json: str = ""
    named_by: str = "fallback"  # fallback | llm | user
    singleton: bool = False  # §6.1 "clearly a new topic" single file (lower priority)
    created_at: datetime = Field(default_factory=now)
