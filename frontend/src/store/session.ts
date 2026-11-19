import { create } from 'zustand'
import { api } from '../api/client'
import type { Card, DecideIn, Edge, FolderOut, Progress, SessionOut, StreamEvent } from '../api/types'
import { openStream } from './ws'

export const PREFETCH_LIMIT = 20
export const REFILL_BELOW = 5
export const UNDO_ALL_STEPS = 1_000_000
export const TREE_DEBOUNCE_MS = 300

export type Outcome = 'moved' | 'trashed' | 'deferred' | 'inbox' | 'kept'

export function outcomeOf(action: Edge['action'], card?: Card): Outcome {
  if (action === 'trash' || action === 'trash_all') return 'trashed'
  if (action === 'dismiss') return 'deferred' // proposal dropped: members come back as cards
  if (action === 'confirm_all') return card?.batch?.kind === 'inbox' ? 'inbox' : 'moved'
  if (action === 'review' || action === 'reject_all' || action === 'suggest_folder') return 'deferred' // members come back (as cards / a proposal)
  if (action === 'defer') return 'deferred'
  if (action === 'inbox') return 'inbox'
  if (action === 'keep_both' || action === 'keep_this') return 'kept' // file continues to classification
  return 'moved'
}

/** keep_this / trash on a duplicate card resolves the duplicate (keep_both just defers the question). */
export function resolvesDuplicate(card: Card, action: Edge['action']): boolean {
  return card.kind === 'duplicate' && (action === 'keep_this' || action === 'trash')
}

/** Batch actions that change files on disk (and are therefore one undo step). */
export function isBatchMutation(card: Card, action: Edge['action']): boolean {
  return !!card.batch && (action === 'confirm_all' || action === 'trash_all')
}

/** Batch actions that only hand the members back as individual cards (nothing to undo). */
export function isBatchDismiss(card: Card, action: Edge['action']): boolean {
  return !!card.batch && (action === 'review' || action === 'reject_all' || action === 'suggest_folder')
}

/** Cards that stand for several files (batch, folder proposal, proposed structure). */
export function isGroup(card: Card): boolean {
  return !!(card.batch || card.proposal || card.taxonomy)
}

/** Files shown on a group card (taxonomy cards list counts, not members). */
export function membersOf(card: Card): number[] {
  if (card.batch) return card.batch.members.map((m) => m.id)
  if (card.proposal) return card.proposal.members.map((m) => m.id)
  return []
}

/** Proposal decisions that move the members (they must not reappear as individual cards). */
export function isProposalMutation(card: Card, action: Edge['action']): boolean {
  return !!card.proposal && (action === 'accept' || action === 'inbox')
}

/** How many files one decision on this card affects. */
export function weightOf(card: Card): number {
  if (card.taxonomy) return 0 // creates folders only; files come back as cards
  return isGroup(card) ? membersOf(card).length : 1
}

/** Edits made on a proposal card before it is decided (sent along with accept). */
export interface ProposalDraft {
  name?: string
  accepted_nodes?: string[]
  renames?: Record<string, string>
}

function undoMessage(entry: UndoEntry): string {
  if (entry.card.taxonomy) return 'Undid: proposed structure'
  if (entry.card.proposal) return `Undid: ${entry.card.proposal.path}/`
  if (!entry.card.batch) return `Undid: ${truncate(entry.card.file.name)}`
  const n = entry.count
  if (entry.outcome === 'trashed') return `Undid ${n} trashed ${n === 1 ? 'file' : 'files'}`
  return `Undid ${n} ${n === 1 ? 'move' : 'moves'}`
}

export interface UndoEntry {
  key: number
  card: Card
  outcome: Outcome
  /** Files affected (batch size; 1 for ordinary cards). */
  count: number
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
  /** True while the WebSocket stream is open (polling is only a fallback while it is not). */
  socketOpen: boolean
  /**
   * card_updated payloads for a card that was on screen (or already decided), keyed by file id.
   * Applied only if that card comes back (undo); never swapped under the user's finger.
   */
  stashedUpdates: Record<number, Card>
  /** In-card edits of proposal cards (name, selected nodes, renames), keyed by card id. */
  drafts: Record<string, ProposalDraft>

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
  /** Apply one server-pushed stream event. */
  applyEvent: (ev: StreamEvent) => void
  dismissToast: () => void
  openPicker: () => void
  closePicker: () => void
  togglePreview: () => void
  openDetails: () => void
  closeDetails: () => void
  setDraft: (cardId: string, draft: ProposalDraft) => void
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

let closeStream: (() => void) | null = null
let treeTimer: ReturnType<typeof setTimeout> | null = null

function stopStream() {
  closeStream?.()
  closeStream = null
  if (treeTimer !== null) clearTimeout(treeTimer)
  treeTimer = null
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
  socketOpen: false,
  stashedUpdates: {} as Record<number, Card>,
  drafts: {} as Record<string, ProposalDraft>,
})

export const useSession = create<SessionState>()((set, get) => ({
  ...initial(),

  start: async (session) => {
    chain = Promise.resolve()
    stopStream()
    set({ ...initial(), sessionId: session.id, session, progress: session.progress })
    closeStream = openStream(session.id, {
      onEvent: (ev) => {
        if (get().sessionId === session.id) get().applyEvent(ev)
      },
      onOpen: (reconnect) => {
        if (get().sessionId !== session.id) return
        set({ socketOpen: true })
        // Events may have been missed while disconnected: catch up from the REST endpoint.
        if (reconnect) void get().refresh()
      },
      onClose: () => {
        if (get().sessionId === session.id) set({ socketOpen: false })
      },
    })
    await get().fetchMore()
  },

  reset: () => {
    stopStream()
    set(initial())
  },

  applyEvent: (ev) => {
    switch (ev.type) {
      case 'hello':
      case 'progress':
        if (ev.progress) set({ progress: ev.progress })
        break
      case 'card_ready':
        if (ev.progress) set({ progress: ev.progress })
        if (ev.card) set((s) => cardReady(s, ev.card as Card))
        break
      case 'card_updated':
        if (ev.progress) set({ progress: ev.progress })
        if (ev.card) set((s) => cardUpdated(s, ev.card as Card))
        break
      case 'card_removed':
        if (ev.progress) set({ progress: ev.progress })
        if (ev.card_id) {
          const id = ev.card_id
          // The visible card (head) is left alone; deciding it later gets a 400, handled silently.
          set((s) => (s.queue.some((c, i) => i > 0 && c.id === id) ? { queue: s.queue.filter((c, i) => i === 0 || c.id !== id) } : {}))
        }
        break
      case 'tree_changed':
        if (treeTimer !== null) clearTimeout(treeTimer)
        treeTimer = setTimeout(() => {
          treeTimer = null
          set((s) => ({ treeVersion: s.treeVersion + 1 }))
        }, TREE_DEBOUNCE_MS)
        break
      case 'op_failed':
        set({ toast: { id: ++toastSeq, kind: 'error', message: ev.message || 'An operation failed', undoable: false } })
        break
      case 'auto_sorted': // watch mode moved a new arrival on its own (undo: Watched folders page)
        set((s) => ({ toast: { id: ++toastSeq, kind: 'info', message: ev.message || 'A new file was sorted', undoable: false }, treeVersion: s.treeVersion + 1 }))
        break
    }
  },

  fetchMore: async () => {
    const { sessionId, fetching } = get()
    if (sessionId === null || fetching) return
    set({ fetching: true })
    try {
      const cards = await api.getCards(sessionId, PREFETCH_LIMIT)
      if (get().sessionId !== sessionId) return
      set((s) => {
        const known = new Set(s.queue.map((c) => c.id))
        const candidates = cards.filter((c) => !known.has(c.id) && !s.inFlightIds.has(c.id) && !isConsumed(s, c))
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
    const outcome = outcomeOf(edge.action, card)
    const dupDelta = resolvesDuplicate(card, edge.action) ? 1 : 0
    const dismiss = isBatchDismiss(card, edge.action)
    const count = dismiss ? 0 : weightOf(card)
    const entry: UndoEntry = { key: ++keySeq, card, outcome, count, dupResolved: dupDelta, label: edge.label }
    const toastId = ++toastSeq
    const body: DecideIn = {
      action: edge.action,
      target_folder_id: edge.target_folder_id ?? null,
      latency_ms: Math.max(0, Date.now() - state.shownAt),
    }
    if (card.batch) body.member_ids = card.batch.members.map((m) => m.id)
    const draft = state.drafts[card.id]
    if (draft && edge.action === 'accept') {
      if (card.proposal && draft.name?.trim()) body.name = draft.name.trim()
      if (card.taxonomy) {
        if (draft.accepted_nodes) body.accepted_nodes = draft.accepted_nodes
        if (draft.renames && Object.keys(draft.renames).length) body.renames = draft.renames
      }
    }
    const memberIds = new Set(membersOf(card))
    const consumed = isBatchMutation(card, edge.action) || isProposalMutation(card, edge.action)
    // Group cards whose outcome changes which cards exist (proposals, structure): refetch after.
    const refetchAfter = dismiss || !!card.proposal || !!card.taxonomy
    // Optimistic advance: the next card shows immediately.
    set((s) => ({
      // Members of a confirmed/trashed batch must not reappear as individual cards.
      queue: consumed ? s.queue.slice(1).filter((c) => !memberIds.has(c.file.id)) : s.queue.slice(1),
      undoStack: dismiss ? s.undoStack : [...s.undoStack, entry],
      counts: { ...s.counts, [outcome]: s.counts[outcome] + count },
      duplicatesResolved: s.duplicatesResolved + dupDelta,
      inFlightIds: new Set(s.inFlightIds).add(card.id),
      flippedIds: without(s.flippedIds, card.id),
      deferredIds: deferWithStash(s, card, outcome),
      exhausted: false,
      previewOpen: false,
      detailsOpen: false,
      shownAt: Date.now(),
      toast: {
        id: toastId,
        kind: 'info',
        message: card.taxonomy
          ? edge.label
          : isGroup(card)
            ? `${membersOf(card).length} ${membersOf(card).length === 1 ? 'file' : 'files'} → ${edge.label}`
            : `${truncate(card.file.name)} → ${edge.label}`,
        undoable: !dismiss,
      },
    }))
    // After review/reject_all the members only exist server-side once the decision lands: refetch then.
    if (!dismiss && get().queue.length < REFILL_BELOW) void get().fetchMore()

    void enqueue(() => api.decide(card.id, body)).then(
      (res) => {
        set((s) => ({
          progress: res.progress,
          treeVersion: s.treeVersion + 1,
          inFlightIds: without(s.inFlightIds, card.id),
        }))
        if (refetchAfter) void get().fetchMore() // members are now individual "m<fid>" cards
      },
      (err) => {
        if (isBadRequest(err)) {
          // 400 "not ready or already decided": the card was removed server-side while on screen.
          // Already advanced past it; quietly forget the decision (no restore, no error toast).
          set((s) => {
            const deferredIds = new Set(s.deferredIds)
            deferredIds.delete(card.id)
            return {
              undoStack: s.undoStack.filter((u) => u.key !== entry.key),
              counts: { ...s.counts, [outcome]: Math.max(0, s.counts[outcome] - count) },
              duplicatesResolved: Math.max(0, s.duplicatesResolved - dupDelta),
              inFlightIds: without(s.inFlightIds, card.id),
              deferredIds,
              toast: s.toast?.id === toastId ? null : s.toast,
            }
          })
          if (get().queue.length < REFILL_BELOW) void get().fetchMore()
          return
        }
        // Roll back: restore the card at the front and drop its undo entry.
        set((s) => {
          const deferredIds = new Set(s.deferredIds)
          deferredIds.delete(card.id)
          return {
            queue: [card, ...s.queue.filter((c) => c.id !== card.id)],
            undoStack: s.undoStack.filter((u) => u.key !== entry.key),
            counts: { ...s.counts, [outcome]: Math.max(0, s.counts[outcome] - count) },
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
        })
      },
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
    // Optimistic: bring the card back right away (with any update that arrived while it was away).
    set((s) => {
      const deferredIds = new Set(s.deferredIds)
      deferredIds.delete(entry.card.id)
      const stashed = isGroup(entry.card) ? undefined : s.stashedUpdates[entry.card.file.id]
      const back = stashed ?? entry.card
      deferredIds.delete(back.id)
      const stashedUpdates = { ...s.stashedUpdates }
      if (!isGroup(entry.card)) delete stashedUpdates[entry.card.file.id]
      return {
        stashedUpdates,
        queue: [back, ...s.queue.filter((c) => c.id !== entry.card.id && c.id !== back.id)],
        undoStack: s.undoStack.filter((u) => u.key !== entry.key),
        counts: { ...s.counts, [entry.outcome]: Math.max(0, s.counts[entry.outcome] - entry.count) },
        duplicatesResolved: Math.max(0, s.duplicatesResolved - entry.dupResolved),
        deferredIds,
        exhausted: false,
        shownAt: Date.now(),
        toast: {
          id: ++toastSeq,
          kind: 'info',
          message: undoMessage(entry),
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
        queue: s.queue.filter((c) => c.id !== entry.card.id && (isGroup(entry.card) || c.file.id !== entry.card.file.id)),
        undoStack: [...s.undoStack, entry],
        counts: { ...s.counts, [entry.outcome]: s.counts[entry.outcome] + entry.count },
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
  setDraft: (cardId, draft) => set((s) => ({ drafts: { ...s.drafts, [cardId]: { ...s.drafts[cardId], ...draft } } })),
}))

function isBadRequest(e: unknown): boolean {
  return typeof e === 'object' && e !== null && (e as { status?: unknown }).status === 400
}

/** Deferred cards are not re-shown this pass; also cover a pending id change for the same file. */
function deferWithStash(s: SessionState, card: Card, outcome: Outcome): Set<string> {
  if (outcome !== 'deferred') return s.deferredIds
  const ids = new Set(s.deferredIds).add(card.id)
  const stashed = isGroup(card) ? undefined : s.stashedUpdates[card.file.id]
  if (stashed) ids.add(stashed.id)
  return ids
}

type QueueState = Pick<SessionState, 'queue' | 'deferredIds' | 'inFlightIds' | 'shownAt' | 'stashedUpdates' | 'undoStack'>

/**
 * Same card by id; or the same file (id changed, d12 -> m12). A batch card's `file` is just a
 * representative member, so batches only ever match by id.
 */
function sameCard(a: Card, b: Card): boolean {
  if (a.id === b.id) return true
  return !isGroup(a) && !isGroup(b) && a.file.id === b.file.id
}

/** True for an individual card whose file was moved/trashed by a confirmed batch (until that is undone). */
function isConsumed(s: Pick<SessionState, 'undoStack'>, card: Card): boolean {
  if (isGroup(card)) return false
  return s.undoStack.some((u) => isGroup(u.card) && u.outcome !== 'deferred' && membersOf(u.card).includes(card.file.id))
}

/** card_ready: append in server order (duplicates stay ahead of everything else), never at the head. */
function cardReady(s: QueueState, card: Card): Partial<SessionState> {
  if (s.deferredIds.has(card.id) || s.inFlightIds.has(card.id) || isConsumed(s, card)) return {}
  if (s.queue.some((c) => sameCard(c, card))) return cardUpdated(s, card)
  if (s.queue.length >= PREFETCH_LIMIT) return {} // fetchMore picks it up (in server order) when the queue drains
  if (s.queue.length === 0) return { queue: [card], shownAt: Date.now() }
  let at = s.queue.length
  if (card.kind === 'duplicate') {
    // After the last duplicate behind the visible card, before every non-duplicate.
    at = 1
    s.queue.forEach((c, i) => {
      if (i > 0 && c.kind === 'duplicate') at = i + 1
    })
  }
  return { queue: [...s.queue.slice(0, at), card, ...s.queue.slice(at)] }
}

/** card_updated: replace by id, or by file id when the card id changed (d12 -> m12). Never the head. */
function cardUpdated(s: QueueState, card: Card): Partial<SessionState> {
  if (isConsumed(s, card)) return {}
  const idx = s.queue.findIndex((c) => sameCard(c, card))
  if (idx > 0) {
    const queue = s.queue.slice()
    queue[idx] = card
    return { queue }
  }
  const decided = !isGroup(card) && s.undoStack.some((u) => !isGroup(u.card) && u.card.file.id === card.file.id)
  if (idx === 0 || decided) {
    // On screen (or decided and possibly coming back via undo): hold the update.
    return { stashedUpdates: { ...s.stashedUpdates, [card.file.id]: card } }
  }
  return {}
}

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
