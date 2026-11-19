"""API contract shared by backend routes and the frontend (mirror in frontend/src/api/types.ts)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Direction = Literal["right", "up", "left", "down"]
Action = Literal[
    "pick", "trash", "defer", "picker", "accept", "choose_1", "choose_2", "choose_3", "inbox",
    "flip",  # client-side: swap card.edges for card.flip_edges (Confirm "Not this")
    "keep_both",  # duplicate: dismiss the duplicate and continue to classification
    "keep_this",  # duplicate: trash the OTHER copy, continue classifying this one
    "details",  # client-side: show the side-by-side duplicate details panel
    "confirm_all",  # batch: move every member to the batch target (one undo step)
    "review",  # batch: dismiss the batch; members come back as individual cards
    "reject_all",  # batch: members come back as individual Choices cards (top pick demoted)
    "trash_all",  # batch: trash every member (one undo step)
    "dismiss",  # proposal: drop it; members come back as individual cards
    "rename",  # proposal: change the proposed name(s) without deciding
    "suggest_folder",  # inbox batch: propose a new folder for these files anyway
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
    live: bool = True  # pipeline running in this daemon (False: left over from an earlier run)
    created_at: datetime | None = None
    kind: str = "manual"  # manual | watch (a watched folder's pending queue)


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


class DuplicateInfo(BaseModel):
    relation: Literal["exact", "near_image", "near_text", "version"]
    similarity: float  # 0..1
    other_name: str
    other_rel_path: str  # relative to its root, posix
    other_side: Literal["input", "output"]
    other_size: int
    other_mtime: float
    other_preview_url: str  # GET-able preview of the other copy
    recommend: Literal["keep_this", "trash_this", "keep_both"]
    reason: str  # e.g. "Identical to Finance/Invoices/x.pdf (100%)"


class BatchInfo(BaseModel):
    kind: Literal["move", "inbox"]  # high-confidence same target, or low-confidence -> _Inbox
    target_folder_id: int
    target_path: str  # relative to output_root, posix ("" = root)
    members: list[FileInfo]  # the exact files the user is confirming (sent back on decide)
    min_confidence: float


class ProposalInfo(BaseModel):
    """Folder proposal: create `path` and move `members` into it."""

    proposal_id: int
    name: str
    parent_folder_id: int | None
    parent_path: str  # relative to output_root, posix ("" = root): the descent stop node
    path: str  # parent_path/name
    inbox_path: str  # where Up sends the members instead
    members: list[FileInfo]  # live members (decided ones drop out)
    cohesion: float
    singleton: bool = False  # a lone file that is clearly a new topic (lower priority)
    named_by: Literal["fallback", "llm", "user"] = "fallback"


class TaxonomyNode(BaseModel):
    id: str  # stable node id ("0", "0.1"); used by accepted_nodes / renames
    name: str
    count: int  # files in this node's subtree
    sample: list[str] = []  # a few member file names
    children: list[TaxonomyNode] = []


class TaxonomyInfo(BaseModel):
    """Cold start "Proposed structure": 2 levels, 5-12 top folders."""

    proposal_id: int
    nodes: list[TaxonomyNode]
    inbox_count: int  # files that fit nowhere (they stay unsorted for now)
    total: int
    named_by: Literal["fallback", "llm", "user"] = "fallback"


class Card(BaseModel):
    id: str  # "m<file_id>" file cards, "d<fid>" duplicates, "b.." batches, "p<id>" proposals
    kind: Literal["manual", "confirm", "choices", "inbox", "duplicate", "batch",
                  "folder_proposal", "taxonomy"]
    duplicate: DuplicateInfo | None = None
    batch: BatchInfo | None = None
    proposal: ProposalInfo | None = None
    taxonomy: TaxonomyInfo | None = None
    destination: str | None = None  # M4: where Right/accept puts it, e.g. "Work/PayPal/_Inbox"
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
    member_ids: list[int] | None = None  # batch cards: the members shown to the user
    latency_ms: int = 0
    name: str | None = None  # folder proposal: accept/rename with this name
    accepted_nodes: list[str] | None = None  # taxonomy: node ids to create (None = all)
    renames: dict[str, str] | None = None  # taxonomy: node id -> new name


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


class StreamEvent(BaseModel):
    """WS /api/sessions/{id}/stream. One JSON object per message."""

    type: Literal["card_ready", "card_updated", "card_removed", "tree_changed", "progress",
                  "op_failed", "hello", "auto_sorted"]
    card: Card | None = None  # card_ready / card_updated
    card_id: str | None = None  # card_removed
    progress: Progress | None = None  # progress (also piggybacked on others)
    message: str = ""  # op_failed / auto_sorted ("Sorted x → Finance/Receipts")
    decision_id: int | None = None  # auto_sorted: undo with POST /api/decisions/{id}/undo


# ---------- watch mode ----------
class WatchFolderIn(BaseModel):
    path: str  # the watched folder (e.g. ~/Downloads)
    output: str  # the output tree its files are sorted into
    enabled: bool = True


class WatchFolderOut(WatchFolderIn):
    session_id: int | None = None  # the folder's long-lived watch session (its pending queue)
    running: bool = False
    pending: int = 0  # READY cards waiting for the user
    processing: int = 0  # settled, still in the pipeline
    error: str = ""


class WatchSafety(BaseModel):
    decisions: int  # user decisions so far (all sessions)
    min_decisions_for_auto: int
    auto_unlocked: bool


class WatchConfigOut(BaseModel):
    enabled: bool
    running: bool
    folders: list[WatchFolderOut]
    settle_seconds: float
    auto_move_threshold: float
    auto_move: bool
    notify: bool
    auto_trash_exact_dupes: bool
    ignore: list[str]
    digest_days: int
    safety: WatchSafety


class WatchConfigIn(BaseModel):
    """PUT /api/watch: only the fields given change (thresholds stay in config.toml)."""

    enabled: bool | None = None
    folders: list[WatchFolderIn] | None = None
    auto_move: bool | None = None
    notify: bool | None = None
    auto_trash_exact_dupes: bool | None = None


class WatchStatus(BaseModel):
    enabled: bool
    running: bool
    pending_total: int
    folders: list[WatchFolderOut]
    safety: WatchSafety


class WatchActivityItem(BaseModel):
    decision_id: int
    session_id: int
    file_id: int | None
    name: str
    action: Literal["move", "trash"]
    src: str  # where it arrived (absolute)
    dst: str  # where it went (absolute)
    dest_rel: str  # destination folder relative to the output tree ("" = root)
    confidence: float | None = None
    ts: datetime
    undone: bool
    can_undo: bool
    undo_blocked: str = ""  # why a targeted undo would be refused


class WatchActivity(BaseModel):
    """The weekly digest: what was sorted automatically, and what's still waiting."""

    days: int
    items: list[WatchActivityItem]
    auto_moved: int
    auto_trashed: int
    pending_total: int
