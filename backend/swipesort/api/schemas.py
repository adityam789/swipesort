"""API contract shared by backend routes and the frontend (mirror in frontend/src/api/types.ts)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Direction = Literal["right", "up", "left", "down"]
Action = Literal[
    "pick", "trash", "defer", "picker", "accept", "choose_1", "choose_2", "choose_3", "inbox",
    "flip",  # client-side: swap card.edges for card.flip_edges (Confirm "Not this")
]


class SessionCreate(BaseModel):
    input_root: str
    output_root: str


class Progress(BaseModel):
    scanned: int = 0
    processed: int = 0  # extracted + embedded + scored (READY or decided)
    ready: int = 0
    decided: int = 0
    remaining: int = 0


class SessionOut(BaseModel):
    id: int
    input_root: str
    output_root: str
    status: str
    progress: Progress


class TreeNode(BaseModel):
    id: int | None = None  # folder id (output side) or None
    name: str
    path: str  # relative to the side's root, posix; "" for root
    is_dir: bool
    is_bundle: bool = False
    count: int = 0  # remaining (input) / file count (output)
    is_new: bool = False  # created during this session
    children: list[TreeNode] = []


class FolderOut(BaseModel):
    id: int
    path: str  # relative to output_root, posix; "" for root
    name: str
    depth: int
    created_by: str


class Edge(BaseModel):
    label: str
    action: Action
    target_folder_id: int | None = None


class FileInfo(BaseModel):
    id: int
    name: str
    rel_path: str
    size: int
    mtime: float
    ext: str
    is_bundle: bool


class Candidate(BaseModel):
    folder_id: int
    path: str  # relative to output_root, posix
    p: float


class Card(BaseModel):
    id: str  # "m<file_id>" for manual cards in M0
    kind: Literal["manual", "confirm", "choices", "inbox", "duplicate", "batch", "proposal"]
    file: FileInfo
    reason: str = ""
    edges: dict[Direction, Edge]  # cards declare their edge targets as data
    flip_edges: dict[Direction, Edge] | None = None  # shown after a "flip" (Confirm -> Choices)
    confidence: float | None = None
    candidates: list[Candidate] = []
    keys: dict[str, Edge] = {}  # extra hotkeys, e.g. {"1": Edge, "2": Edge, "3": Edge}


class DecideIn(BaseModel):
    action: Action
    target_folder_id: int | None = None
    latency_ms: int = 0


class DecideOut(BaseModel):
    decision_id: int
    final_path: str | None = None
    progress: Progress


class UndoIn(BaseModel):
    steps: int = 1


class UndoOut(BaseModel):
    undone_ops: list[int]
    progress: Progress


class FolderCreate(BaseModel):
    session_id: int
    parent_id: int | None = None  # None -> output root
    name: str


class BrowseEntry(BaseModel):
    name: str
    path: str  # absolute
    is_dir: bool


class BrowseOut(BaseModel):
    path: str
    parent: str | None
    entries: list[BrowseEntry]
