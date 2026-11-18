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

export interface SessionCreate {
  input_root: string
  output_root: string
}

export interface Progress {
  scanned: number
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

export interface Card {
  id: string
  kind: CardKind
  file: FileInfo
  reason: string
  edges: Partial<Record<Direction, Edge>>
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

export const DIRECTIONS: Direction[] = ['up', 'right', 'down', 'left']
