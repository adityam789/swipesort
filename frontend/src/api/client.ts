import type {
  BrowseOut,
  Card,
  DecideIn,
  DecideOut,
  FolderCreate,
  FolderOut,
  SessionCreate,
  SessionOut,
  TreeNode,
  UndoIn,
  UndoOut,
} from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* ignore non-JSON error bodies */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`)
  }
  return (await res.json()) as T
}

function post<T>(url: string, body: unknown): Promise<T> {
  return request<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export const api = {
  createSession: (body: SessionCreate) => post<SessionOut>('/api/sessions', body),
  getSession: (id: number) => request<SessionOut>(`/api/sessions/${id}`),
  getTree: (id: number, side: 'input' | 'output') =>
    request<TreeNode>(`/api/sessions/${id}/tree?side=${side}`),
  getCards: (id: number, limit = 20) =>
    request<Card[]>(`/api/sessions/${id}/cards?limit=${limit}`),
  getFolders: (id: number) => request<FolderOut[]>(`/api/sessions/${id}/folders`),
  decide: (cardId: string, body: DecideIn) =>
    post<DecideOut>(`/api/cards/${encodeURIComponent(cardId)}/decide`, body),
  undo: (id: number, body: UndoIn) => post<UndoOut>(`/api/sessions/${id}/undo`, body),
  createFolder: (body: FolderCreate) => post<FolderOut>('/api/folders', body),
  browse: (path?: string) =>
    request<BrowseOut>(`/api/fs/browse?path=${encodeURIComponent(path ?? '')}`),
}

export const previewUrl = (fileId: number) => `/api/files/${fileId}/preview`

export type PreviewData =
  | { kind: 'image'; url: string }
  | { kind: 'text'; text: string }

interface RawPreview {
  type: string
  blob: Blob
}

const PREVIEW_CACHE_MAX = 8
const rawCache = new Map<number, Promise<RawPreview | null>>()

function loadRaw(fileId: number): Promise<RawPreview | null> {
  const hit = rawCache.get(fileId)
  if (hit) return hit
  const p = fetch(previewUrl(fileId))
    .then(async (res) => (res.ok ? { type: res.headers.get('content-type') ?? '', blob: await res.blob() } : null))
    .catch(() => null)
  rawCache.set(fileId, p)
  void p.then((v) => {
    if (v === null) rawCache.delete(fileId)
  })
  while (rawCache.size > PREVIEW_CACHE_MAX) {
    const oldest = rawCache.keys().next().value
    if (oldest === undefined) break
    rawCache.delete(oldest)
  }
  return p
}

/** Warm the preview cache so the card renders instantly when it reaches the front. */
export function prefetchPreview(fileId: number): void {
  void loadRaw(fileId)
}

/** Fetch a preview (cached); image/* becomes a fresh blob URL (caller revokes), text/* becomes a string. */
export async function fetchPreview(fileId: number, signal?: AbortSignal): Promise<PreviewData | null> {
  const raw = await loadRaw(fileId)
  if (!raw || signal?.aborted) return null
  if (raw.type.startsWith('image/')) return { kind: 'image', url: URL.createObjectURL(raw.blob) }
  if (raw.type.startsWith('text/')) return { kind: 'text', text: await raw.blob.text() }
  return null
}
