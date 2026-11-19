// Mirrors backend/swipesort/api/schemas.py. Keep in sync.

export type Direction = 'right' | 'up' | 'left' | 'down'
export type Action =
  | 'pick'
  | 'trash'
  | 'defer'
  | 'picker'
  | 'accept'
  | 'choose_1'
  | 'choose_2'
  | 'choose_3'
  | 'inbox'
  | 'flip' // client-side only: swap card.edges for card.flip_edges (Confirm "Not this")
  | 'keep_both' // duplicate: dismiss the duplicate and continue to classification
  | 'keep_this' // duplicate: trash the OTHER copy, continue classifying this one
  | 'details' // client-side only: show the side-by-side duplicate details panel
  | 'confirm_all' // batch: move every member to the batch target (one undo step)
  | 'review' // batch: dismiss the batch; members come back as individual cards
  | 'reject_all' // batch: members come back as individual Choices cards
  | 'trash_all' // batch: trash every member (one undo step)
  | 'dismiss' // proposal: drop it; members come back as individual cards
  | 'rename' // proposal: change the proposed name(s) without deciding
  | 'suggest_folder' // inbox batch: propose a new folder for these files anyway

export interface SessionCreate {
  input_root: string
  output_root: string
}

export interface Progress {
  scanned: number
  processed: number
  ready: number
  decided: number
  remaining: number
}

export interface SessionOut {
  id: number
  input_root: string
  output_root: string
  status: string
  progress: Progress
  /** Pipeline running in this daemon run (false: left over from an earlier run). */
  live?: boolean
  created_at?: string | null
  /** manual | watch (a watched folder's pending queue, SPEC §18). */
  kind?: string
}

// Phone pairing (SPEC §12 POST /api/pair; backend api/pairing.py)
export interface PairStatus {
  /** Request came from the computer running the daemon (desktop). */
  local: boolean
  paired: boolean
  /** Daemon listens on the LAN (`swipesort serve --lan`). */
  lan: boolean
  device_id: number | null
}

export interface PairStart {
  code: string
  url: string
  lan: boolean
  lan_ip: string
  expires_at: string
  expires_in: number
}

export interface PairOut {
  device_id: number
  token: string
  expires_at: string | null
}

export interface DeviceOut {
  id: number
  name: string
  user_agent: string
  created_at: string
  last_seen: string
  expires_at: string | null
}

export interface TreeNode {
  id: number | null
  name: string
  path: string
  is_dir: boolean
  is_bundle: boolean
  count: number
  is_new: boolean
  children: TreeNode[]
}

export interface FolderOut {
  id: number
  path: string
  name: string
  depth: number
  created_by: string
}

export interface Edge {
  label: string
  action: Action
  target_folder_id?: number | null
  /** Client-side rendering hint (not sent by the server): emphasise this edge. */
  highlight?: boolean
}

export interface FileInfo {
  id: number
  name: string
  rel_path: string
  size: number
  mtime: number
  ext: string
  is_bundle: boolean
}

export type CardKind =
  | 'manual'
  | 'confirm'
  | 'choices'
  | 'inbox'
  | 'duplicate'
  | 'batch'
  | 'folder_proposal'
  | 'taxonomy'

export interface Candidate {
  folder_id: number
  path: string
  p: number
}

export type DuplicateRelation = 'exact' | 'near_image' | 'near_text' | 'version'
export type DuplicateRecommend = 'keep_this' | 'trash_this' | 'keep_both'

export interface DuplicateInfo {
  relation: DuplicateRelation
  similarity: number // 0..1
  other_name: string
  other_rel_path: string // relative to its root, posix
  other_side: 'input' | 'output'
  other_size: number
  other_mtime: number
  other_preview_url: string // GET-able preview of the other copy
  recommend: DuplicateRecommend
  reason: string
}

export interface BatchInfo {
  kind: 'move' | 'inbox' // high-confidence same target, or low-confidence -> _Inbox
  target_folder_id: number
  target_path: string // relative to output_root, posix ("" = root)
  members: FileInfo[] // the exact files the user is confirming (sent back on decide)
  min_confidence: number
}

export type NamedBy = 'fallback' | 'llm' | 'user'

/** Folder proposal (SPEC §6.1): create `path` and move `members` into it. */
export interface ProposalInfo {
  proposal_id: number
  name: string
  parent_folder_id: number | null
  parent_path: string // relative to output_root, posix ("" = root): the descent stop node
  path: string // parent_path/name
  inbox_path: string // where Up sends the members instead
  members: FileInfo[]
  cohesion: number
  singleton: boolean
  named_by: NamedBy
}

export interface TaxonomyNode {
  id: string // stable node id ("0", "0.1"); used by accepted_nodes / renames
  name: string
  count: number
  sample: string[]
  children: TaxonomyNode[]
}

/** Cold start "Proposed structure" (SPEC §6.2). */
export interface TaxonomyInfo {
  proposal_id: number
  nodes: TaxonomyNode[]
  inbox_count: number
  total: number
  named_by: NamedBy
}

export interface Card {
  id: string
  kind: CardKind
  duplicate?: DuplicateInfo | null
  batch?: BatchInfo | null
  proposal?: ProposalInfo | null
  taxonomy?: TaxonomyInfo | null
  /** Where Right/accept puts the file, e.g. "Work/PayPal/_Inbox". */
  destination?: string | null
  file: FileInfo
  reason: string
  edges: Partial<Record<Direction, Edge>>
  flip_edges?: Partial<Record<Direction, Edge>> | null
  confidence?: number | null
  candidates: Candidate[]
  keys: Record<string, Edge>
}

export interface DecideIn {
  action: Action
  target_folder_id?: number | null
  /** Batch cards: the members shown to the user, exactly as displayed. */
  member_ids?: number[] | null
  latency_ms?: number
  /** Folder proposal: accept/rename with this name. */
  name?: string | null
  /** Taxonomy: node ids to create (omitted = all). */
  accepted_nodes?: string[] | null
  /** Taxonomy: node id -> new name. */
  renames?: Record<string, string> | null
}

export interface DecideOut {
  decision_id: number
  final_path: string | null
  progress: Progress
}

export interface UndoIn {
  steps: number
}

export interface UndoOut {
  undone_ops: number[]
  progress: Progress
}

export interface FolderCreate {
  session_id: number
  parent_id?: number | null
  name: string
}

export interface BrowseEntry {
  name: string
  path: string
  is_dir: boolean
}

export interface BrowseOut {
  path: string
  parent: string | null
  entries: BrowseEntry[]
}

export type StreamEventType =
  | 'card_ready'
  | 'card_updated'
  | 'card_removed'
  | 'tree_changed'
  | 'progress'
  | 'op_failed'
  | 'hello'
  | 'auto_sorted'

/** One JSON message on WS /api/sessions/{id}/stream. */
export interface StreamEvent {
  type: StreamEventType
  card?: Card | null // card_ready / card_updated
  card_id?: string | null // card_removed
  progress?: Progress | null // progress (also piggybacked on others)
  message?: string // op_failed / auto_sorted
  decision_id?: number | null // auto_sorted: POST /api/decisions/{id}/undo
}

export const DIRECTIONS: Direction[] = ['up', 'right', 'down', 'left']

// Watch mode (SPEC §18; backend api/schemas.py "watch mode")
export interface WatchFolderIn {
  path: string
  output: string
  enabled?: boolean
}

export interface WatchFolderOut extends WatchFolderIn {
  session_id: number | null
  running: boolean
  pending: number
  processing: number
  error: string
}

export interface WatchSafety {
  decisions: number
  min_decisions_for_auto: number
  auto_unlocked: boolean
}

export interface WatchConfig {
  enabled: boolean
  running: boolean
  folders: WatchFolderOut[]
  settle_seconds: number
  auto_move_threshold: number
  auto_move: boolean
  notify: boolean
  auto_trash_exact_dupes: boolean
  ignore: string[]
  digest_days: number
  safety: WatchSafety
}

export interface WatchConfigIn {
  enabled?: boolean
  folders?: WatchFolderIn[]
  auto_move?: boolean
  notify?: boolean
  auto_trash_exact_dupes?: boolean
}

export interface WatchStatus {
  enabled: boolean
  running: boolean
  pending_total: number
  folders: WatchFolderOut[]
  safety: WatchSafety
}

export interface WatchActivityItem {
  decision_id: number
  session_id: number
  file_id: number | null
  name: string
  action: 'move' | 'trash'
  src: string
  dst: string
  dest_rel: string
  confidence: number | null
  ts: string
  undone: boolean
  can_undo: boolean
  undo_blocked: string
}

export interface WatchActivity {
  days: number
  items: WatchActivityItem[]
  auto_moved: number
  auto_trashed: number
  pending_total: number
}
