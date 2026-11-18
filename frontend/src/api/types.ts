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
  | 'proposal'

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

export interface Card {
  id: string
  kind: CardKind
  duplicate?: DuplicateInfo | null
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
  latency_ms?: number
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

/** One JSON message on WS /api/sessions/{id}/stream. */
export interface StreamEvent {
  type: StreamEventType
  card?: Card | null // card_ready / card_updated
  card_id?: string | null // card_removed
  progress?: Progress | null // progress (also piggybacked on others)
  message?: string // op_failed
}

export const DIRECTIONS: Direction[] = ['up', 'right', 'down', 'left']
