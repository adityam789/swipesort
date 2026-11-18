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

/** Fetch a preview once; image/* becomes a blob URL (caller revokes), text/* becomes a string. */
export async function fetchPreview(fileId: number, signal?: AbortSignal): Promise<PreviewData | null> {
  const res = await fetch(previewUrl(fileId), { signal })
  if (!res.ok) return null
  const type = res.headers.get('content-type') ?? ''
  if (type.startsWith('image/')) return { kind: 'image', url: URL.createObjectURL(await res.blob()) }
  if (type.startsWith('text/')) return { kind: 'text', text: await res.text() }
  return null
}
