import { create } from 'zustand'
import { api } from '../api/client'
import type { Card, DecideIn, Edge, FolderOut, Progress, SessionOut } from '../api/types'

export const PREFETCH_LIMIT = 20
export const REFILL_BELOW = 5
export const UNDO_ALL_STEPS = 1_000_000

export type Outcome = 'moved' | 'trashed' | 'deferred' | 'inbox' | 'kept'

export function outcomeOf(action: Edge['action']): Outcome {
  if (action === 'trash') return 'trashed'
  if (action === 'defer') return 'deferred'
  if (action === 'inbox') return 'inbox'
  if (action === 'keep_both' || action === 'keep_this') return 'kept' // file continues to classification
  return 'moved'
}

/** keep_this / trash on a duplicate card resolves the duplicate (keep_both just defers the question). */
export function resolvesDuplicate(card: Card, action: Edge['action']): boolean {
  return card.kind === 'duplicate' && (action === 'keep_this' || action === 'trash')
}

export interface UndoEntry {
  key: number
  card: Card
  outcome: Outcome
  /** 1 when this decision counted toward `duplicatesResolved`. */
  dupResolved: number
  label: string
}

export interface Toast {
  id: number
  kind: 'info' | 'error'
  message: string
  undoable: boolean
}

const emptyProgress: Progress = { scanned: 0, processed: 0, ready: 0, decided: 0, remaining: 0 }

export interface SessionState {
  sessionId: number | null
  session: SessionOut | null
  progress: Progress
  queue: Card[]
  /** Cards decided (or deferred) locally; never shown again this pass. */
  deferredIds: Set<string>
  inFlightIds: Set<string>
  exhausted: boolean
  fetching: boolean
  undoStack: UndoEntry[]
  counts: Record<Outcome, number>
  /** Duplicate cards resolved with keep_this or trash. */
  duplicatesResolved: number
  /** Side-by-side duplicate compare panel (client-side only). */
  detailsOpen: boolean
  toast: Toast | null
  lastFolder: FolderOut | null
  pickerOpen: boolean
  previewOpen: boolean
  /** Bumped after each decision/undo so trees & folder lists refetch. */
  treeVersion: number
  shownAt: number

  start: (session: SessionOut) => Promise<void>
  reset: () => void
  fetchMore: () => Promise<void>
  /** Poll session progress + cards (used while files are still being analyzed). */
  refresh: () => Promise<void>
  decide: (card: Card, edge: Edge) => void
  /** Client-side "Not this": swap the front card's edges for its flip_edges. No network call. */
  flip: (cardId: string) => void
  /** Cards currently showing their flipped layout. */
  flippedIds: Set<string>
  pickFolder: (folder: FolderOut) => void
  undo: () => Promise<void>
  undoAll: () => Promise<void>
  setProgress: (p: Progress) => void
  dismissToast: () => void
  openPicker: () => void
  closePicker: () => void
  togglePreview: () => void
  openDetails: () => void
  closeDetails: () => void
}

let keySeq = 0
let toastSeq = 0
// Serialises server mutations so undo always lands after the decision it targets.
let chain: Promise<unknown> = Promise.resolve()
function enqueue<T>(fn: () => Promise<T>): Promise<T> {
  const next = chain.then(fn, fn)
  chain = next.catch(() => undefined)
  return next
}

const EMPTY_COUNTS = (): Record<Outcome, number> => ({ moved: 0, trashed: 0, deferred: 0, inbox: 0, kept: 0 })

const initial = () => ({
  sessionId: null,
  session: null,
  progress: emptyProgress,
  queue: [] as Card[],
  deferredIds: new Set<string>(),
  inFlightIds: new Set<string>(),
  flippedIds: new Set<string>(),
  exhausted: false,
  fetching: false,
  undoStack: [] as UndoEntry[],
  counts: EMPTY_COUNTS(),
  duplicatesResolved: 0,
  detailsOpen: false,
  toast: null as Toast | null,
  lastFolder: null as FolderOut | null,
  pickerOpen: false,
  previewOpen: false,
  treeVersion: 0,
  shownAt: Date.now(),
})

export const useSession = create<SessionState>()((set, get) => ({
  ...initial(),

  start: async (session) => {
    chain = Promise.resolve()
    set({ ...initial(), sessionId: session.id, session, progress: session.progress })
    await get().fetchMore()
  },

  reset: () => set(initial()),

  fetchMore: async () => {
    const { sessionId, fetching } = get()
    if (sessionId === null || fetching) return
    set({ fetching: true })
    try {
      const cards = await api.getCards(sessionId, PREFETCH_LIMIT)
      if (get().sessionId !== sessionId) return
      set((s) => {
        const known = new Set(s.queue.map((c) => c.id))
        const candidates = cards.filter((c) => !known.has(c.id) && !s.inFlightIds.has(c.id))
        let fresh = candidates.filter((c) => !s.deferredIds.has(c.id))
        let deferredIds = s.deferredIds
        // Deferred cards come back once everything else is done (the server orders them last).
        if (fresh.length === 0 && s.queue.length === 0 && candidates.length > 0) {
          fresh = candidates
          deferredIds = new Set<string>()
        }
        const queue = [...s.queue, ...fresh]
        return {
          queue,
          deferredIds,
          exhausted: fresh.length === 0,
          shownAt: s.queue.length === 0 && fresh.length > 0 ? Date.now() : s.shownAt,
        }
      })
    } catch (e) {
      set({ toast: { id: ++toastSeq, kind: 'error', message: errMsg(e), undoable: false } })
    } finally {
      set({ fetching: false })
    }
  },

  refresh: async () => {
    const { sessionId } = get()
    if (sessionId === null) return
    try {
      // Session first, cards second: if the snapshot says everything is processed, the
      // cards fetched afterwards are guaranteed to include everything that is READY.
      const session = await api.getSession(sessionId)
      if (get().sessionId !== sessionId) return
      await get().fetchMore()
      if (get().sessionId !== sessionId) return
      set({ progress: session.progress })
    } catch (e) {
      set({ toast: { id: ++toastSeq, kind: 'error', message: errMsg(e), undoable: false } })
    }
  },

  flip: (cardId) => {
    set((s) => {
      const card = s.queue[0]
      if (!card || card.id !== cardId || !card.flip_edges) return {}
      const flipped: Card = {
        ...card,
        kind: 'choices',
        edges: card.flip_edges,
        flip_edges: null,
        keys: Object.keys(card.keys).length ? card.keys : numberKeys(card.flip_edges),
      }
      return { queue: [flipped, ...s.queue.slice(1)], flippedIds: new Set(s.flippedIds).add(cardId) }
    })
  },

  decide: (card, edge) => {
    const state = get()
    if (state.sessionId === null || state.queue[0]?.id !== card.id) return
    const outcome = outcomeOf(edge.action)
    const dupDelta = resolvesDuplicate(card, edge.action) ? 1 : 0
    const entry: UndoEntry = { key: ++keySeq, card, outcome, dupResolved: dupDelta, label: edge.label }
    const body: DecideIn = {
      action: edge.action,
      target_folder_id: edge.target_folder_id ?? null,
      latency_ms: Math.max(0, Date.now() - state.shownAt),
    }
    // Optimistic advance: the next card shows immediately.
    set((s) => ({
      queue: s.queue.slice(1),
      undoStack: [...s.undoStack, entry],
      counts: { ...s.counts, [outcome]: s.counts[outcome] + 1 },
      duplicatesResolved: s.duplicatesResolved + dupDelta,
      inFlightIds: new Set(s.inFlightIds).add(card.id),
      flippedIds: without(s.flippedIds, card.id),
      deferredIds: outcome === 'deferred' ? new Set(s.deferredIds).add(card.id) : s.deferredIds,
      exhausted: false,
      previewOpen: false,
      detailsOpen: false,
      shownAt: Date.now(),
      toast: {
        id: ++toastSeq,
        kind: 'info',
        message: `${truncate(card.file.name)} → ${edge.label}`,
        undoable: true,
      },
    }))
    if (get().queue.length < REFILL_BELOW) void get().fetchMore()

    void enqueue(() => api.decide(card.id, body)).then(
      (res) =>
        set((s) => ({
          progress: res.progress,
          treeVersion: s.treeVersion + 1,
          inFlightIds: without(s.inFlightIds, card.id),
        })),
      (err) =>
        // Roll back: restore the card at the front and drop its undo entry.
        set((s) => {
          const deferredIds = new Set(s.deferredIds)
          deferredIds.delete(card.id)
          return {
            queue: [card, ...s.queue.filter((c) => c.id !== card.id)],
            undoStack: s.undoStack.filter((u) => u.key !== entry.key),
            counts: { ...s.counts, [outcome]: Math.max(0, s.counts[outcome] - 1) },
            duplicatesResolved: Math.max(0, s.duplicatesResolved - dupDelta),
            inFlightIds: without(s.inFlightIds, card.id),
            deferredIds,
            shownAt: Date.now(),
            toast: {
              id: ++toastSeq,
              kind: 'error',
              message: `Couldn't apply that: ${errMsg(err)}`,
              undoable: false,
            },
          }
        }),
    )
  },

  pickFolder: (folder) => {
    const card = get().queue[0]
    set({ pickerOpen: false, lastFolder: folder })
    if (!card) return
    get().decide(card, {
      label: `Move to ${folder.path || folder.name}`,
      action: 'pick',
      target_folder_id: folder.id,
    })
  },

  undo: async () => {
    const { sessionId, undoStack } = get()
    const entry = undoStack[undoStack.length - 1]
    if (sessionId === null || !entry) return
    // Optimistic: bring the card back right away.
    set((s) => {
      const deferredIds = new Set(s.deferredIds)
      deferredIds.delete(entry.card.id)
      return {
        queue: [entry.card, ...s.queue.filter((c) => c.id !== entry.card.id)],
        undoStack: s.undoStack.filter((u) => u.key !== entry.key),
        counts: { ...s.counts, [entry.outcome]: Math.max(0, s.counts[entry.outcome] - 1) },
        duplicatesResolved: Math.max(0, s.duplicatesResolved - entry.dupResolved),
        deferredIds,
        exhausted: false,
        shownAt: Date.now(),
        toast: {
          id: ++toastSeq,
          kind: 'info',
          message: `Undid: ${truncate(entry.card.file.name)}`,
          undoable: false,
        },
      }
    })
    try {
      const res = await enqueue(() => api.undo(sessionId, { steps: 1 }))
      set((s) => ({ progress: res.progress, treeVersion: s.treeVersion + 1 }))
    } catch (err) {
      // Put things back the way they were.
      set((s) => ({
        queue: s.queue.filter((c) => c.id !== entry.card.id),
        undoStack: [...s.undoStack, entry],
        counts: { ...s.counts, [entry.outcome]: s.counts[entry.outcome] + 1 },
        duplicatesResolved: s.duplicatesResolved + entry.dupResolved,
        deferredIds: entry.outcome === 'deferred' ? new Set(s.deferredIds).add(entry.card.id) : s.deferredIds,
        toast: { id: ++toastSeq, kind: 'error', message: `Undo failed: ${errMsg(err)}`, undoable: false },
      }))
    }
  },

  undoAll: async () => {
    const { sessionId } = get()
    if (sessionId === null) return
    try {
      const res = await enqueue(() => api.undo(sessionId, { steps: UNDO_ALL_STEPS }))
      set((s) => ({
        progress: res.progress,
        treeVersion: s.treeVersion + 1,
        queue: [],
        undoStack: [],
        counts: EMPTY_COUNTS(),
        duplicatesResolved: 0,
        deferredIds: new Set<string>(),
        exhausted: false,
        toast: { id: ++toastSeq, kind: 'info', message: 'Undid everything', undoable: false },
      }))
      await get().fetchMore()
    } catch (err) {
      set({ toast: { id: ++toastSeq, kind: 'error', message: `Undo failed: ${errMsg(err)}`, undoable: false } })
    }
  },

  setProgress: (progress) => set({ progress }),
  dismissToast: () => set({ toast: null }),
  openPicker: () => set((s) => (s.queue.length ? { pickerOpen: true } : {})),
  closePicker: () => set({ pickerOpen: false }),
  togglePreview: () => set((s) => ({ previewOpen: s.queue.length ? !s.previewOpen : false })),
  openDetails: () => set((s) => (s.queue[0]?.duplicate ? { detailsOpen: true } : {})),
  closeDetails: () => set({ detailsOpen: false }),
}))

/** Hotkeys 1/2/3 for a pick layout, used when the backend sent no explicit `keys`. */
function numberKeys(edges: Card['edges']): Record<string, Edge> {
  const keys: Record<string, Edge> = {}
  for (const dir of ['right', 'up', 'left'] as const) {
    const e = edges[dir]
    if (e?.action === 'pick') keys[String(Object.keys(keys).length + 1)] = e
  }
  return keys
}

function without(set: Set<string>, id: string): Set<string> {
  const next = new Set(set)
  next.delete(id)
  return next
}

function truncate(name: string, n = 32): string {
  return name.length > n ? `${name.slice(0, n - 1)}…` : name
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
