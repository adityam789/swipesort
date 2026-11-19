import type {
  BrowseOut,
  Card,
  DeviceOut,
  PairOut,
  PairStart,
  PairStatus,
  DecideIn,
  DecideOut,
  FolderCreate,
  FolderOut,
  SessionCreate,
  SessionOut,
  TreeNode,
  UndoIn,
  UndoOut,
  WatchActivity,
  WatchConfig,
  WatchConfigIn,
  WatchStatus,
} from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** Fired when a paired phone's token stops working (revoked/expired): the app shows pairing. */
export const UNAUTHORIZED_EVENT = 'swipesort:unauthorized'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (res.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
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
  if (res.status === 204) return undefined as T
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
  listSessions: () => request<SessionOut[]>('/api/sessions'),
  pairStatus: () => request<PairStatus>('/api/pair/status'),
  pairStart: () => post<PairStart>('/api/pair/start', {}),
  pair: (code: string, name: string) => post<PairOut>('/api/pair', { code, name }),
  devices: () => request<DeviceOut[]>('/api/devices'),
  revokeDevice: (id: number) => request<void>(`/api/devices/${id}`, { method: 'DELETE' }),
  getSession: (id: number) => request<SessionOut>(`/api/sessions/${id}`),
  getTree: (id: number, side: 'input' | 'output') =>
    request<TreeNode>(`/api/sessions/${id}/tree?side=${side}`),
  getCards: (id: number, limit = 20) =>
    request<Card[]>(`/api/sessions/${id}/cards?limit=${limit}`),
  getFolders: (id: number) => request<FolderOut[]>(`/api/sessions/${id}/folders`),
  decide: (cardId: string, body: DecideIn) =>
    post<DecideOut>(`/api/cards/${encodeURIComponent(cardId)}/decide`, body),
  decideProposal: (proposalId: number, body: DecideIn) =>
    post<DecideOut>(`/api/proposals/${proposalId}/decide`, body),
  undo: (id: number, body: UndoIn) => post<UndoOut>(`/api/sessions/${id}/undo`, body),
  createFolder: (body: FolderCreate) => post<FolderOut>('/api/folders', body),
  browse: (path?: string) =>
    request<BrowseOut>(`/api/fs/browse?path=${encodeURIComponent(path ?? '')}`),
  // watch mode (SPEC §18): desktop-only endpoints
  getWatch: () => request<WatchConfig>('/api/watch'),
  putWatch: (body: WatchConfigIn) =>
    request<WatchConfig>('/api/watch', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  watchStatus: () => request<WatchStatus>('/api/watch/status'),
  watchActivity: (days?: number) =>
    request<WatchActivity>(`/api/watch/activity${days ? `?days=${days}` : ''}`),
  /** Targeted undo of one decision (an auto-move); 409 when later changes depend on it. */
  undoDecision: (decisionId: number) => post<UndoOut>(`/api/decisions/${decisionId}/undo`, {}),
}

export const pairQrUrl = (code: string) => `/api/pair/qr.svg?code=${encodeURIComponent(code)}`

export const previewUrl = (fileId: number) => `/api/files/${fileId}/preview`

export type PreviewData =
  | { kind: 'image'; url: string }
  | { kind: 'text'; text: string }

interface RawPreview {
  type: string
  blob: Blob
}

const PREVIEW_CACHE_MAX = 8
const rawCache = new Map<string, Promise<RawPreview | null>>()

/** A preview source is a file id (served by /api/files/{id}/preview) or an explicit URL. */
export type PreviewSource = number | string
const urlOf = (src: PreviewSource) => (typeof src === 'number' ? previewUrl(src) : src)

function loadRaw(src: PreviewSource): Promise<RawPreview | null> {
  const url = urlOf(src)
  const hit = rawCache.get(url)
  if (hit) return hit
  const p = fetch(url)
    .then(async (res) => (res.ok ? { type: res.headers.get('content-type') ?? '', blob: await res.blob() } : null))
    .catch(() => null)
  rawCache.set(url, p)
  void p.then((v) => {
    if (v === null) rawCache.delete(url)
  })
  while (rawCache.size > PREVIEW_CACHE_MAX) {
    const oldest = rawCache.keys().next().value
    if (oldest === undefined) break
    rawCache.delete(oldest)
  }
  return p
}

/** Warm the preview cache so the card renders instantly when it reaches the front. */
export function prefetchPreview(src: PreviewSource): void {
  void loadRaw(src)
}

/** Fetch a preview (cached); image/* becomes a fresh blob URL (caller revokes), text/* becomes a string. */
export async function fetchPreview(src: PreviewSource, signal?: AbortSignal): Promise<PreviewData | null> {
  const raw = await loadRaw(src)
  if (!raw || signal?.aborted) return null
  if (raw.type.startsWith('image/')) return { kind: 'image', url: URL.createObjectURL(raw.blob) }
  if (raw.type.startsWith('text/')) return { kind: 'text', text: await raw.blob.text() }
  return null
}
