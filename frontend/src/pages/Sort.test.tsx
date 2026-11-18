import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Card, Edge, Progress, SessionOut } from '../api/types'

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
})
