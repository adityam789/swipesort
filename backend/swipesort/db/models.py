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
