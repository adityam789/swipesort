import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Card, Edge, Progress, SessionOut } from '../api/types'

vi.mock('../api/client', () => ({
  api: { getCards: vi.fn(), getSession: vi.fn(), decide: vi.fn(), undo: vi.fn() },
}))

import { api } from '../api/client'
import { useSession } from './session'

const progress: Progress = { scanned: 3, processed: 3, ready: 3, decided: 0, remaining: 3 }
const session: SessionOut = { id: 1, input_root: '/in', output_root: '/out', status: 'active', progress }
const mk = (n: number): Card => ({
  id: `m${n}`,
  kind: 'manual',
  reason: '',
  candidates: [],
  keys: {},
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

  describe('M1 behavior', () => {
    const pick = (id: number, label: string): Edge => ({ label, action: 'pick', target_folder_id: id })
    const confirmCard: Card = {
      ...mk(1),
      kind: 'confirm',
      confidence: 0.95,
      edges: {
        right: pick(1, 'Move to Receipts'),
        up: { label: 'Later', action: 'defer' },
        left: { label: 'Not this', action: 'flip' },
        down: trash,
      },
      flip_edges: { right: pick(1, 'Receipts'), up: pick(2, 'Taxes'), left: pick(3, 'Misc'), down: trash },
    }

    it('flip swaps edges locally without any network call', async () => {
      getCards.mockResolvedValue([confirmCard, mk(2)])
      await useSession.getState().start(session)
      getCards.mockClear()
      useSession.getState().flip('m1')
      const front = useSession.getState().queue[0]
      expect(front.edges.left).toEqual(pick(3, 'Misc'))
      expect(front.kind).toBe('choices')
      expect(front.keys['2']).toEqual(pick(2, 'Taxes'))
      expect(useSession.getState().queue).toHaveLength(2)
      expect(decide).not.toHaveBeenCalled()
      expect(undo).not.toHaveBeenCalled()
      expect(getCards).not.toHaveBeenCalled()
      expect(useSession.getState().undoStack).toHaveLength(0)
    })

    it('posts the inbox action and counts inbox separately', async () => {
      getCards.mockResolvedValue([mk(1)])
      await useSession.getState().start(session)
      decide.mockResolvedValue({ decision_id: 1, final_path: null, progress })
      useSession.getState().decide(useSession.getState().queue[0], { label: 'Send to _Inbox', action: 'inbox' })
      await vi.waitFor(() => expect(decide).toHaveBeenCalled())
      expect(decide).toHaveBeenCalledWith('m1', expect.objectContaining({ action: 'inbox' }))
      const { counts } = useSession.getState()
      expect(counts.inbox).toBe(1)
      expect(counts.moved).toBe(0)
    })

    it('refresh polls the session and cards, publishing progress after the cards load', async () => {
      getCards.mockResolvedValue([])
      await useSession.getState().start({ ...session, progress: { ...progress, processed: 1 } })
      getCards.mockResolvedValue([mk(9)])
      vi.mocked(api.getSession).mockResolvedValue({ ...session, progress: { ...progress, processed: 3 } })
      await useSession.getState().refresh()
      expect(useSession.getState().queue.map((c) => c.id)).toEqual(['m9'])
      expect(useSession.getState().progress.processed).toBe(3)
    })

    it('brings deferred cards back once everything else is done', async () => {
      getCards.mockResolvedValue([mk(1)])
      await useSession.getState().start(session)
      decide.mockResolvedValue({ decision_id: 1, final_path: null, progress })
      useSession.getState().decide(useSession.getState().queue[0], { label: 'Later', action: 'defer' })
      await vi.waitFor(() => expect(useSession.getState().inFlightIds.size).toBe(0))
      await useSession.getState().fetchMore()
      expect(useSession.getState().queue.map((c) => c.id)).toEqual(['m1'])
    })
  })
  describe('batch cards', () => {
    const batchCard = (members: number[], over: Partial<Card> = {}): Card => ({
      ...mk(members[0]),
      id: 'b1',
      kind: 'batch',
      batch: {
        kind: 'move',
        target_folder_id: 5,
        target_path: 'Finance',
        members: members.map((n) => mk(n).file),
        min_confidence: 0.96,
      },
      edges: {
        right: { label: 'Confirm all', action: 'confirm_all' },
        up: { label: 'Review', action: 'review' },
        left: { label: 'Reject all', action: 'reject_all' },
        down: { label: 'Trash all', action: 'trash_all' },
      },
      ...over,
    })
    const ok = () => decide.mockResolvedValue({ decision_id: 1, final_path: null, progress })

    it('sends member_ids exactly as shown and counts every member as moved', async () => {
      getCards.mockResolvedValue([batchCard([1, 2, 3]), mk(9)])
      await useSession.getState().start(session)
      ok()
      useSession.getState().decide(useSession.getState().queue[0], { label: 'Confirm all', action: 'confirm_all' })
      await vi.waitFor(() => expect(decide).toHaveBeenCalledWith('b1', expect.objectContaining({ action: 'confirm_all', member_ids: [1, 2, 3] })))
      const s = useSession.getState()
      expect(s.counts.moved).toBe(3)
      expect(s.undoStack).toHaveLength(1)
      expect(s.queue.map((c) => c.id)).toEqual(['m9'])
    })

    it('counts trash_all members as trashed and keeps them from reappearing', async () => {
      getCards.mockResolvedValue([batchCard([1, 2]), mk(9)])
      await useSession.getState().start(session)
      ok()
      useSession.getState().decide(useSession.getState().queue[0], { label: 'Trash all', action: 'trash_all' })
      await vi.waitFor(() => expect(useSession.getState().inFlightIds.size).toBe(0))
      expect(useSession.getState().counts.trashed).toBe(2)
      // stale member cards from the server / stream are ignored
      getCards.mockResolvedValue([mk(1), mk(2), mk(9)])
      await useSession.getState().fetchMore()
      useSession.getState().applyEvent({ type: 'card_ready', card: mk(2) })
      expect(useSession.getState().queue.map((c) => c.id)).toEqual(['m9'])
    })

    it('accepts member m-cards after review without counting anything', async () => {
      getCards.mockResolvedValue([batchCard([1, 2])])
      await useSession.getState().start(session)
      ok()
      getCards.mockResolvedValue([mk(1), mk(2)])
      useSession.getState().decide(useSession.getState().queue[0], { label: 'Review', action: 'review' })
      await vi.waitFor(() => expect(decide).toHaveBeenCalledWith('b1', expect.objectContaining({ action: 'review', member_ids: [1, 2] })))
      await vi.waitFor(() => expect(useSession.getState().queue.map((c) => c.id)).toEqual(['m1', 'm2']))
      const s = useSession.getState()
      expect(s.counts.moved + s.counts.trashed + s.counts.deferred).toBe(0)
      expect(s.undoStack).toHaveLength(0)
    })

    it('accepts members that stream in after reject_all', async () => {
      getCards.mockResolvedValue([batchCard([1, 2])])
      await useSession.getState().start(session)
      ok()
      getCards.mockResolvedValue([])
      useSession.getState().decide(useSession.getState().queue[0], { label: 'Reject all', action: 'reject_all' })
      await vi.waitFor(() => expect(useSession.getState().inFlightIds.size).toBe(0))
      useSession.getState().applyEvent({ type: 'card_ready', card: mk(1) })
      useSession.getState().applyEvent({ type: 'card_ready', card: mk(2) })
      expect(useSession.getState().queue.map((c) => c.id)).toEqual(['m1', 'm2'])
    })

    it('undo of a batch is one step and the toast pluralizes', async () => {
      getCards.mockResolvedValue([batchCard([1, 2, 3, 4]), mk(9)])
      await useSession.getState().start(session)
      ok()
      undo.mockResolvedValue({ undone_ops: [1, 2, 3, 4], progress })
      useSession.getState().decide(useSession.getState().queue[0], { label: 'Confirm all', action: 'confirm_all' })
      await useSession.getState().undo()
      expect(undo).toHaveBeenCalledTimes(1)
      expect(undo).toHaveBeenCalledWith(1, { steps: 1 })
      const s = useSession.getState()
      expect(s.toast?.message).toBe('Undid 4 moves')
      expect(s.counts.moved).toBe(0)
      expect(s.queue[0].id).toBe('b1')
    })

    it('uses singular for a one-file batch', async () => {
      getCards.mockResolvedValue([batchCard([1])])
      await useSession.getState().start(session)
      ok()
      undo.mockResolvedValue({ undone_ops: [1], progress })
      useSession.getState().decide(useSession.getState().queue[0], { label: 'Confirm all', action: 'confirm_all' })
      await useSession.getState().undo()
      expect(useSession.getState().toast?.message).toBe('Undid 1 move')
    })

    it('rolls back counts when a batch decision fails', async () => {
      getCards.mockResolvedValue([batchCard([1, 2, 3])])
      await useSession.getState().start(session)
      decide.mockRejectedValue(new Error('boom'))
      useSession.getState().decide(useSession.getState().queue[0], { label: 'Confirm all', action: 'confirm_all' })
      await vi.waitFor(() => expect(useSession.getState().queue[0]?.id).toBe('b1'))
      expect(useSession.getState().counts.moved).toBe(0)
    })
  })

  it('proposal accept sends the edited name, consumes members and refetches', async () => {
    const prop: Card = {
      ...mk(9),
      id: 'p9',
      kind: 'folder_proposal',
      edges: { right: { label: 'Create Loans/', action: 'accept' } },
      proposal: {
        proposal_id: 9, name: 'Loans', parent_folder_id: 1, parent_path: '', path: 'Loans', inbox_path: '_Inbox',
        members: [mk(2).file, mk(3).file], cohesion: 0.8, singleton: false, named_by: 'fallback',
      },
    }
    getCards.mockResolvedValueOnce([prop, mk(1), mk(2)])
    await useSession.getState().start(session)
    decide.mockResolvedValue({ decision_id: 5, final_path: null, progress })
    getCards.mockResolvedValue([mk(1), mk(4)])
    useSession.getState().setDraft('p9', { name: ' Home Loans ' })
    useSession.getState().decide(prop, prop.edges.right as Edge)
    const s = useSession.getState()
    expect(s.queue.map((c) => c.id)).toEqual(['m1']) // member m2 is consumed
    expect(s.counts.moved).toBe(2)
    expect(s.undoStack.at(-1)?.count).toBe(2)
    await vi.waitFor(() => expect(useSession.getState().queue.map((c) => c.id)).toEqual(['m1', 'm4']))
    expect(decide).toHaveBeenCalledWith('p9', expect.objectContaining({ action: 'accept', name: 'Home Loans' }))
  })

  it('taxonomy accept sends the selected nodes and renames, and counts no files', async () => {
    const tax: Card = {
      ...mk(9),
      id: 'p3',
      kind: 'taxonomy',
      edges: { right: { label: 'Create these folders', action: 'accept' } },
      taxonomy: { proposal_id: 3, nodes: [], inbox_count: 0, total: 3, named_by: 'fallback' },
    }
    getCards.mockResolvedValueOnce([tax])
    await useSession.getState().start(session)
    decide.mockResolvedValue({ decision_id: 6, final_path: null, progress })
    getCards.mockResolvedValue([mk(1)])
    useSession.getState().setDraft('p3', { accepted_nodes: ['0'], renames: { '0': 'Money' } })
    useSession.getState().decide(tax, tax.edges.right as Edge)
    expect(useSession.getState().counts.moved).toBe(0)
    await vi.waitFor(() => expect(useSession.getState().queue.map((c) => c.id)).toEqual(['m1']))
    expect(decide).toHaveBeenCalledWith('p3', expect.objectContaining({ accepted_nodes: ['0'], renames: { '0': 'Money' } }))
  })
})
