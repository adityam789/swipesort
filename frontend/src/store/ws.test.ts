import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Card, Progress, SessionOut, StreamEvent } from '../api/types'

vi.mock('../api/client', () => ({
  api: { getCards: vi.fn(), getSession: vi.fn(), decide: vi.fn(), undo: vi.fn() },
}))

import { api } from '../api/client'
import { useSession } from './session'
import { backoffMs } from './ws'

class MockWebSocket {
  static instances: MockWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  url: string
  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }
  close() {
    this.closed = true
  }
  // test helpers
  open() {
    this.onopen?.()
  }
  emit(ev: StreamEvent) {
    this.onmessage?.({ data: JSON.stringify(ev) })
  }
  drop() {
    this.onclose?.()
  }
}
const sock = () => MockWebSocket.instances[MockWebSocket.instances.length - 1]

const progress: Progress = { scanned: 5, processed: 5, ready: 5, decided: 0, remaining: 5 }
const session: SessionOut = { id: 7, input_root: '/in', output_root: '/out', status: 'active', progress }
const mk = (n: number, extra: Partial<Card> = {}): Card => ({
  id: `m${n}`,
  kind: 'manual',
  reason: '',
  candidates: [],
  keys: {},
  file: { id: n, name: `f${n}.txt`, rel_path: `f${n}.txt`, size: 1, mtime: 0, ext: '.txt', is_bundle: false },
  edges: { down: { label: 'Trash', action: 'trash' } },
  ...extra,
})
const dup = (n: number): Card => ({ ...mk(n), id: `d${n}`, kind: 'duplicate' })
const ids = () => useSession.getState().queue.map((c) => c.id)
const st = () => useSession.getState()

const getCards = vi.mocked(api.getCards)
const decide = vi.mocked(api.decide)
const undo = vi.mocked(api.undo)

beforeEach(() => {
  vi.useFakeTimers()
  MockWebSocket.instances = []
  vi.stubGlobal('WebSocket', MockWebSocket)
  vi.resetAllMocks()
  getCards.mockResolvedValue([mk(1), mk(2), mk(3)])
  st().reset()
})
afterEach(() => {
  st().reset()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function started() {
  await st().start(session)
  sock().open()
}

describe('stream connection', () => {
  it('connects to the session stream, flags socketOpen, and closes on reset', async () => {
    await started()
    expect(sock().url).toMatch(/^wss?:\/\/.+\/api\/sessions\/7\/stream$/)
    expect(st().socketOpen).toBe(true)
    st().reset()
    expect(sock().closed).toBe(true)
    expect(st().socketOpen).toBe(false)
  })

  it('reconnects with capped exponential backoff, resets after success, and catches up', async () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(backoffMs)).toEqual([250, 500, 1000, 2000, 4000, 5000, 5000])
    await started()
    expect(MockWebSocket.instances).toHaveLength(1)
    sock().drop()
    expect(st().socketOpen).toBe(false)
    vi.advanceTimersByTime(249)
    expect(MockWebSocket.instances).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(MockWebSocket.instances).toHaveLength(2)
    sock().drop() // fails again without opening: next delay is 500
    vi.advanceTimersByTime(499)
    expect(MockWebSocket.instances).toHaveLength(2)
    vi.advanceTimersByTime(1)
    expect(MockWebSocket.instances).toHaveLength(3)
    vi.mocked(api.getSession).mockResolvedValue(session)
    getCards.mockClear()
    sock().open()
    expect(st().socketOpen).toBe(true)
    await vi.waitFor(() => expect(getCards).toHaveBeenCalled()) // reconnect catch-up
    sock().drop()
    vi.advanceTimersByTime(250) // backoff was reset by the successful open
    expect(MockWebSocket.instances).toHaveLength(4)
  })

  it('does not reconnect after reset', async () => {
    await started()
    st().reset()
    vi.advanceTimersByTime(10_000)
    expect(MockWebSocket.instances).toHaveLength(1)
  })
})

describe('stream events', () => {
  it('never swaps the visible card; applies the update only if it comes back', async () => {
    await started()
    decide.mockResolvedValue({ decision_id: 1, final_path: null, progress })
    undo.mockResolvedValue({ undone_ops: [1], progress })
    const updated = mk(1, { id: 'm1', reason: 'new reason' })
    sock().emit({ type: 'card_updated', card: updated })
    expect(st().queue[0].reason).toBe('') // untouched on screen
    expect(st().queue[0]).not.toBe(updated)

    st().decide(st().queue[0], { label: 'Trash', action: 'trash' })
    expect(ids()).toEqual(['m2', 'm3'])
    await st().undo()
    expect(st().queue[0].reason).toBe('new reason')
    expect(st().stashedUpdates).toEqual({})
  })

  it('matches the visible card by file id when its id changes (d -> m)', async () => {
    getCards.mockResolvedValue([dup(1), mk(2)])
    await started()
    decide.mockResolvedValue({ decision_id: 1, final_path: null, progress })
    undo.mockResolvedValue({ undone_ops: [1], progress })
    sock().emit({ type: 'card_updated', card: mk(1) }) // d1 -> m1
    expect(ids()).toEqual(['d1', 'm2'])
    st().decide(st().queue[0], { label: 'Trash', action: 'trash' })
    await st().undo()
    expect(ids()).toEqual(['m1', 'm2'])
  })

  it('replaces a non-visible card, including an id change d -> m for the same file', async () => {
    getCards.mockResolvedValue([mk(1), dup(2), mk(3)])
    await started()
    sock().emit({ type: 'card_updated', card: mk(3, { reason: 'fresh' }) })
    expect(st().queue[2].reason).toBe('fresh')
    sock().emit({ type: 'card_updated', card: mk(2) }) // d2 -> m2
    expect(ids()).toEqual(['m1', 'm2', 'm3'])
    expect(st().queue[1].kind).toBe('manual')
  })

  it('card_removed drops a non-visible card but leaves the visible one', async () => {
    await started()
    sock().emit({ type: 'card_removed', card_id: 'm3' })
    expect(ids()).toEqual(['m1', 'm2'])
    sock().emit({ type: 'card_removed', card_id: 'm1' })
    expect(ids()).toEqual(['m1', 'm2'])
  })

  it('deciding a removed visible card (400) skips silently with no error toast', async () => {
    await started()
    sock().emit({ type: 'card_removed', card_id: 'm1' })
    getCards.mockResolvedValue([mk(2), mk(3)]) // server no longer lists it
    decide.mockRejectedValue(Object.assign(new Error('not ready or already decided'), { status: 400 }))
    st().decide(st().queue[0], { label: 'Trash', action: 'trash' })
    await vi.waitFor(() => expect(st().inFlightIds.size).toBe(0))
    expect(ids()).toEqual(['m2', 'm3'])
    expect(st().undoStack).toHaveLength(0)
    expect(st().counts.trashed).toBe(0)
    expect(st().toast).toBeNull()
  })

  it('card_ready appends new cards, ignores known ones, keeps duplicates ahead', async () => {
    getCards.mockResolvedValue([dup(1), dup(2), mk(3)])
    await started()
    sock().emit({ type: 'card_ready', card: mk(4) })
    expect(ids()).toEqual(['d1', 'd2', 'm3', 'm4'])
    sock().emit({ type: 'card_ready', card: dup(5) })
    expect(ids()).toEqual(['d1', 'd2', 'd5', 'm3', 'm4'])
    sock().emit({ type: 'card_ready', card: mk(4) })
    expect(ids()).toEqual(['d1', 'd2', 'd5', 'm3', 'm4'])
  })

  it('card_ready never lands in front of the visible card', async () => {
    getCards.mockResolvedValue([mk(1), mk(2)])
    await started()
    sock().emit({ type: 'card_ready', card: dup(9) })
    expect(ids()).toEqual(['m1', 'd9', 'm2'])
  })

  it('card_ready skips cards deferred this pass', async () => {
    await started()
    decide.mockResolvedValue({ decision_id: 1, final_path: null, progress })
    st().decide(st().queue[0], { label: 'Later', action: 'defer' })
    sock().emit({ type: 'card_ready', card: mk(1) })
    expect(ids()).toEqual(['m2', 'm3'])
  })

  it('progress/hello update progress; op_failed toasts; tree_changed debounces a tree refetch', async () => {
    await started()
    sock().emit({ type: 'progress', progress: { ...progress, processed: 2, scanned: 9 } })
    expect(st().progress.processed).toBe(2)
    expect(st().progress.scanned).toBe(9)
    sock().emit({ type: 'op_failed', message: 'file is locked' })
    expect(st().toast).toMatchObject({ kind: 'error', message: 'file is locked' })

    const v = st().treeVersion
    sock().emit({ type: 'tree_changed' })
    vi.advanceTimersByTime(200)
    sock().emit({ type: 'tree_changed' })
    vi.advanceTimersByTime(299)
    expect(st().treeVersion).toBe(v)
    vi.advanceTimersByTime(1)
    expect(st().treeVersion).toBe(v + 1)
  })
})
