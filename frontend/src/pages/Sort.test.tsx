import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Card, DuplicateInfo, Edge, Progress, SessionOut } from '../api/types'

vi.mock('../api/client', () => ({
  api: {
    getCards: vi.fn(),
    getSession: vi.fn(),
    getFolders: vi.fn(() => Promise.resolve([])),
    getTree: vi.fn(),
    decide: vi.fn(),
    undo: vi.fn(),
  },
  fetchPreview: () => Promise.resolve(null),
  prefetchPreview: vi.fn(),
}))
vi.mock('../trees/InputTree', () => ({ InputTree: () => null }))
vi.mock('../trees/OutputTree', () => ({ OutputTree: () => null }))

import { api } from '../api/client'
import { useSession } from '../store/session'
import { Sort } from './Sort'

const pick = (id: number, label: string): Edge => ({ label, action: 'pick', target_folder_id: id })
const trash: Edge = { label: 'Trash', action: 'trash' }
const mk = (n: number, extra: Partial<Card> = {}): Card => ({
  id: `m${n}`,
  kind: 'manual',
  reason: '',
  candidates: [],
  keys: {},
  file: { id: n, name: `f${n}.txt`, rel_path: `f${n}.txt`, size: 1, mtime: 0, ext: '.txt', is_bundle: false },
  edges: { down: trash },
  ...extra,
})
const prog = (p: Partial<Progress>): Progress => ({ scanned: 2, processed: 2, ready: 2, decided: 0, remaining: 2, ...p })
const sess = (progress: Progress): SessionOut => ({ id: 1, input_root: '/i', output_root: '/o', status: 'active', progress })

const getCards = vi.mocked(api.getCards)
const getSession = vi.mocked(api.getSession)
const decide = vi.mocked(api.decide)

function renderSort() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Sort sessionId={1} onNew={() => undefined} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getFolders).mockResolvedValue([])
  decide.mockResolvedValue({ decision_id: 1, final_path: null, progress: prog({}) })
  useSession.getState().reset()
})

describe('Sort page', () => {
  it('flips Confirm -> Choices on "Not this" without any network call', async () => {
    const confirm = mk(1, {
      kind: 'confirm',
      confidence: 0.95,
      edges: { right: pick(1, 'Move to Receipts'), left: { label: 'Not this', action: 'flip' }, down: trash },
      flip_edges: { right: pick(1, 'Receipts'), up: pick(2, 'Taxes'), left: pick(3, 'Misc'), down: trash },
    })
    getCards.mockResolvedValue([confirm, mk(2)])
    await useSession.getState().start(sess(prog({})))
    getCards.mockClear()
    renderSort()
    const user = userEvent.setup()
    await user.click(await screen.findByText('Not this'))
    expect(await screen.findByText('Taxes')).toBeInTheDocument()
    expect(screen.queryByText('Not this')).toBeNull()
    expect(decide).not.toHaveBeenCalled()
    expect(getCards).not.toHaveBeenCalled()
    // after the flip, 3 maps to the third choice
    await user.keyboard('3')
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1))
    expect(decide).toHaveBeenCalledWith('m1', expect.objectContaining({ action: 'pick', target_folder_id: 3 }))
  })

  it('keys 1/2/3 trigger the edges declared in card.keys', async () => {
    const edges = { right: pick(10, 'One'), up: pick(20, 'Two'), left: pick(30, 'Three'), down: trash }
    const choices = mk(1, { kind: 'choices', edges, keys: { '1': edges.right, '2': edges.up, '3': edges.left } })
    getCards.mockResolvedValue([choices, mk(2)])
    await useSession.getState().start(sess(prog({})))
    renderSort()
    await screen.findByText('Two')
    await userEvent.setup().keyboard('2')
    await waitFor(() => expect(decide).toHaveBeenCalled())
    expect(decide).toHaveBeenCalledWith('m1', expect.objectContaining({ action: 'pick', target_folder_id: 20 }))
  })

  it('posts {action: "inbox"} from the inbox card and counts it in the summary', async () => {
    const inbox = mk(1, {
      kind: 'inbox',
      edges: { right: { label: 'Send to _Inbox', action: 'inbox' }, down: trash },
    })
    getCards.mockResolvedValue([inbox])
    getSession.mockResolvedValue(sess(prog({ scanned: 1, processed: 1, decided: 1, remaining: 0 })))
    await useSession.getState().start(sess(prog({ scanned: 1, processed: 1 })))
    renderSort()
    await userEvent.setup().click(await screen.findByText('Send to _Inbox'))
    await waitFor(() => expect(decide).toHaveBeenCalled())
    expect(decide).toHaveBeenCalledWith('m1', expect.objectContaining({ action: 'inbox' }))
    getCards.mockResolvedValue([])
    await act(() => useSession.getState().fetchMore())
    const label = await screen.findByText('Inbox')
    expect(label.previousElementSibling).toHaveTextContent('1')
  })

  it('shows "Analyzing files… n/m" while the queue is empty and files are still processing', async () => {
    getCards.mockResolvedValue([])
    getSession.mockResolvedValue(sess(prog({ scanned: 5, processed: 2 })))
    await useSession.getState().start(sess(prog({ scanned: 5, processed: 2 })))
    renderSort()
    expect(await screen.findByText('Analyzing files… 2/5')).toBeInTheDocument()
    expect(screen.queryByText('All sorted')).toBeNull()
  })

  it('goes to Summary only once processed == scanned and no cards remain', async () => {
    getCards.mockResolvedValue([])
    getSession.mockResolvedValue(sess(prog({ scanned: 5, processed: 5 })))
    await useSession.getState().start(sess(prog({ scanned: 5, processed: 2 })))
    renderSort()
    expect(await screen.findByText(/Analyzing files/)).toBeInTheDocument()
    await act(() => useSession.getState().refresh())
    expect(await screen.findByText('All sorted')).toBeInTheDocument()
  })

  describe('duplicate cards', () => {
    const info: DuplicateInfo = {
      relation: 'exact',
      similarity: 1,
      other_name: 'twin.txt',
      other_rel_path: 'Docs/twin.txt',
      other_side: 'output',
      other_size: 2048,
      other_mtime: 1_700_000_000,
      other_preview_url: '/api/files/77/preview',
      recommend: 'trash_this',
      reason: 'Identical to Docs/twin.txt (100%)',
    }
    const dupCard = (n: number): Card =>
      mk(n, {
        id: `d${n}`,
        kind: 'duplicate',
        duplicate: info,
        edges: {
          right: { label: 'Keep both', action: 'keep_both' },
          up: { label: 'Keep this, trash the other', action: 'keep_this' },
          left: { label: 'Details', action: 'details' },
          down: { label: 'Trash this one', action: 'trash' },
        },
      })

    it('Details opens the compare panel with no network call; Esc closes it', async () => {
      getCards.mockResolvedValue([dupCard(1), mk(2)])
      await useSession.getState().start(sess(prog({})))
      getCards.mockClear()
      renderSort()
      const user = userEvent.setup()
      await user.click(await screen.findByText('Details'))
      const panel = await screen.findByTestId('duplicate-panel')
      expect(panel).toHaveTextContent('Docs/twin.txt')
      expect(panel).toHaveTextContent('f1.txt')
      expect(panel).toHaveTextContent('100%')
      expect(panel).toHaveTextContent('Identical to Docs/twin.txt')
      expect(decide).not.toHaveBeenCalled()
      expect(getCards).not.toHaveBeenCalled()
      expect(useSession.getState().undoStack).toHaveLength(0)
      await user.keyboard('{Escape}')
      await waitFor(() => expect(screen.queryByTestId('duplicate-panel')).toBeNull())
      expect(decide).not.toHaveBeenCalled()
    })

    it('keep_both posts, and the same file coming back as an "m<id>" card is accepted', async () => {
      getCards.mockResolvedValue([dupCard(1), mk(2)])
      await useSession.getState().start(sess(prog({})))
      useSession.getState().decide(useSession.getState().queue[0], { label: 'Keep both', action: 'keep_both' })
      await waitFor(() => expect(useSession.getState().inFlightIds.size).toBe(0))
      expect(decide).toHaveBeenCalledWith('d1', expect.objectContaining({ action: 'keep_both' }))
      expect(useSession.getState().queue.map((c) => c.id)).toEqual(['m2'])
      // Server re-queues file 1 as a classification card with a different card id.
      getCards.mockResolvedValue([mk(2), mk(1)])
      await act(() => useSession.getState().fetchMore())
      expect(useSession.getState().queue.map((c) => c.id)).toEqual(['m2', 'm1'])
      expect(useSession.getState().queue[1].file.id).toBe(1)
    })

    it('counts duplicates resolved (keep_this + trash) separately in the summary; keep_both does not count', async () => {
      getCards.mockResolvedValue([dupCard(1), dupCard(2), dupCard(3)])
      getSession.mockResolvedValue(sess(prog({ scanned: 3, processed: 3, decided: 3, remaining: 0 })))
      await useSession.getState().start(sess(prog({ scanned: 3, processed: 3 })))
      const s = () => useSession.getState()
      s().decide(s().queue[0], { label: 'Keep this, trash the other', action: 'keep_this' })
      s().decide(s().queue[0], { label: 'Trash this one', action: 'trash' })
      s().decide(s().queue[0], { label: 'Keep both', action: 'keep_both' })
      expect(s().duplicatesResolved).toBe(2)
      await waitFor(() => expect(s().inFlightIds.size).toBe(0))
      getCards.mockResolvedValue([])
      await act(() => s().fetchMore())
      renderSort()
      const label = await screen.findByText('Duplicates resolved')
      expect(label.previousElementSibling).toHaveTextContent('2')
    })

    it('undo of keep_both restores the duplicate card at the front', async () => {
      getCards.mockResolvedValue([dupCard(1), mk(2)])
      vi.mocked(api.undo).mockResolvedValue({ undone_ops: [1], progress: prog({}) })
      await useSession.getState().start(sess(prog({})))
      useSession.getState().decide(useSession.getState().queue[0], { label: 'Keep both', action: 'keep_both' })
      await waitFor(() => expect(useSession.getState().inFlightIds.size).toBe(0))
      await act(() => useSession.getState().undo())
      expect(useSession.getState().queue[0].id).toBe('d1')
      getCards.mockResolvedValue([dupCard(1), mk(2)]) // server refetch after undo must not be filtered out
      await act(() => useSession.getState().fetchMore())
      expect(useSession.getState().queue.map((c) => c.id)).toEqual(['d1', 'm2'])
    })
  })

  describe('stream vs polling', () => {
    it('polls only while the socket is not open and shows the analyzing indicator', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      try {
        getCards.mockResolvedValue([mk(1), mk(2)])
        getSession.mockResolvedValue(sess(prog({ scanned: 5, processed: 2 })))
        await useSession.getState().start(sess(prog({ scanned: 5, processed: 2 })))
        useSession.setState({ socketOpen: false, queue: [] })
        renderSort()
        getSession.mockClear()
        await act(() => vi.advanceTimersByTimeAsync(1100))
        expect(getSession).toHaveBeenCalled() // polling while the socket is down

        act(() => useSession.setState({ socketOpen: true }))
        getSession.mockClear()
        await act(() => vi.advanceTimersByTimeAsync(10_000))
        expect(getSession).not.toHaveBeenCalled() // stopped while open

        act(() => useSession.setState({ socketOpen: false }))
        await act(() => vi.advanceTimersByTimeAsync(3100))
        expect(getSession).toHaveBeenCalled() // resumed after a drop
        expect(screen.getByTestId('analyzing')).toHaveTextContent('analyzing 2/5')
      } finally {
        vi.useRealTimers()
      }
    })
  })
})
