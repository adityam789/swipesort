import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Card, Progress, SessionOut } from '../api/types'

vi.mock('../api/client', () => ({
  api: { getCards: vi.fn(), decide: vi.fn(), undo: vi.fn() },
}))

import { api } from '../api/client'
import { useSession } from './session'

const progress: Progress = { scanned: 3, ready: 3, decided: 0, remaining: 3 }
const session: SessionOut = { id: 1, input_root: '/in', output_root: '/out', status: 'active', progress }
const mk = (n: number): Card => ({
  id: `m${n}`,
  kind: 'manual',
  reason: '',
  file: { id: n, name: `f${n}.txt`, rel_path: `f${n}.txt`, size: 1, mtime: 0, ext: '.txt', is_bundle: false },
  edges: { down: { label: 'Trash', action: 'trash' }, up: { label: 'Later', action: 'defer' } },
})
const trash = { label: 'Trash', action: 'trash' } as const

const getCards = vi.mocked(api.getCards)
const decide = vi.mocked(api.decide)
const undo = vi.mocked(api.undo)

beforeEach(() => {
  vi.resetAllMocks()
  getCards.mockResolvedValue([mk(1), mk(2), mk(3)])
})

describe('session store', () => {
  it('loads cards on start', async () => {
    await useSession.getState().start(session)
    expect(useSession.getState().queue.map((c) => c.id)).toEqual(['m1', 'm2', 'm3'])
  })

  it('advances optimistically before the server responds', async () => {
    await useSession.getState().start(session)
    let resolve!: (v: Awaited<ReturnType<typeof api.decide>>) => void
    decide.mockReturnValue(new Promise((r) => (resolve = r)))

    useSession.getState().decide(useSession.getState().queue[0], trash)
    const s = useSession.getState()
    expect(s.queue.map((c) => c.id)).toEqual(['m2', 'm3']) // already advanced
    expect(s.counts.trashed).toBe(1)
    expect(s.undoStack).toHaveLength(1)

    resolve({ decision_id: 1, final_path: null, progress: { ...progress, decided: 1, remaining: 2 } })
    await vi.waitFor(() => expect(useSession.getState().progress.decided).toBe(1))
    expect(decide).toHaveBeenCalledWith('m1', expect.objectContaining({ action: 'trash' }))
  })

  it('rolls back and shows an error toast when the decision fails', async () => {
    await useSession.getState().start(session)
    decide.mockRejectedValue(new Error('boom'))
    useSession.getState().decide(useSession.getState().queue[0], trash)
    expect(useSession.getState().queue[0].id).toBe('m2')

    await vi.waitFor(() => expect(useSession.getState().queue[0].id).toBe('m1'))
    const s = useSession.getState()
    expect(s.undoStack).toHaveLength(0)
    expect(s.counts.trashed).toBe(0)
    expect(s.toast?.kind).toBe('error')
    expect(s.toast?.message).toContain('boom')
  })

  it('does not re-show deferred cards and undo restores the card', async () => {
    await useSession.getState().start(session)
    decide.mockResolvedValue({ decision_id: 1, final_path: null, progress })
    undo.mockResolvedValue({ undone_ops: [1], progress })
    useSession.getState().decide(useSession.getState().queue[0], { label: 'Later', action: 'defer' })
    await vi.waitFor(() => expect(decide).toHaveBeenCalled())
    await useSession.getState().fetchMore() // backend would return m1 again (skipped, sorted last)
    expect(useSession.getState().queue.map((c) => c.id)).toEqual(['m2', 'm3'])

    await useSession.getState().undo()
    expect(useSession.getState().queue[0].id).toBe('m1')
    expect(undo).toHaveBeenCalledWith(1, { steps: 1 })
  })

  it('refills when fewer than 5 cards remain', async () => {
    await useSession.getState().start(session)
    getCards.mockClear()
    decide.mockResolvedValue({ decision_id: 1, final_path: null, progress })
    useSession.getState().decide(useSession.getState().queue[0], trash)
    await vi.waitFor(() => expect(getCards).toHaveBeenCalled())
  })
})
