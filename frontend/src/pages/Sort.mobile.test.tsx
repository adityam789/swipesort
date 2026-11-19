import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Card, Progress, SessionOut } from '../api/types'
import { setMobile } from '../test/media'

vi.mock('../api/client', () => ({
  api: {
    getCards: vi.fn(),
    getSession: vi.fn(),
    getFolders: vi.fn(() => Promise.resolve([])),
    getTree: vi.fn(),
    decide: vi.fn(),
    undo: vi.fn(),
    pairStatus: vi.fn(),
    devices: vi.fn(() => Promise.resolve([])),
  },
  fetchPreview: () => Promise.resolve(null),
  prefetchPreview: vi.fn(),
}))
vi.mock('../trees/InputTree', () => ({ InputTree: () => <p>input-tree-body</p> }))
vi.mock('../trees/OutputTree', () => ({ OutputTree: () => <p>output-tree-body</p> }))

import { api } from '../api/client'
import { useSession } from '../store/session'
import { Sort } from './Sort'

const card: Card = {
  id: 'm1',
  kind: 'manual',
  reason: '',
  candidates: [],
  keys: {},
  file: { id: 1, name: 'f1.txt', rel_path: 'f1.txt', size: 1, mtime: 0, ext: '.txt', is_bundle: false },
  edges: { down: { label: 'Trash', action: 'trash' }, left: { label: 'Choose folder…', action: 'picker' } },
}
const progress: Progress = { scanned: 1, processed: 1, ready: 1, decided: 0, remaining: 1 }
const session: SessionOut = { id: 1, input_root: '/i', output_root: '/o', status: 'active', progress }

async function renderSort(local = true) {
  vi.mocked(api.getCards).mockResolvedValue([card])
  vi.mocked(api.getSession).mockResolvedValue(session)
  await act(() => useSession.getState().start(session))
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Sort sessionId={1} onNew={() => undefined} local={local} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.getFolders).mockResolvedValue([])
  useSession.getState().reset()
})
afterEach(() => vi.unstubAllGlobals())

describe('Sort layout', () => {
  it('phone: full-screen card, trees and picker as bottom sheets, undo button', async () => {
    setMobile(true)
    await renderSort()
    await screen.findByTestId('card')
    expect(screen.getByRole('main')).toHaveAttribute('data-layout', 'mobile')
    expect(document.querySelector('[data-fullscreen]')).not.toBeNull()
    expect(screen.queryByRole('complementary', { name: 'Input tree' })).toBeNull()
    expect(screen.queryByText('input-tree-body')).toBeNull()
    expect(screen.getByRole('button', { name: /Undo/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pair phone' })).toBeNull()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Input' }))
    const sheet = screen.getByRole('dialog', { name: 'Input tree' })
    expect(sheet).toHaveAttribute('data-sheet', 'true')
    expect(sheet).toHaveTextContent('input-tree-body')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Input tree' })).toBeNull())

    await user.click(screen.getByRole('button', { name: /Choose folder/ }))
    expect(await screen.findByRole('dialog', { name: 'Choose folder' })).toHaveAttribute('data-sheet', 'true')
  })

  it('desktop: side trees, no drawer buttons, pair button', async () => {
    setMobile(false)
    await renderSort()
    await screen.findByTestId('card')
    expect(screen.getByRole('main')).toHaveAttribute('data-layout', 'desktop')
    expect(screen.getByRole('complementary', { name: 'Input tree' })).toHaveTextContent('input-tree-body')
    expect(screen.getByRole('complementary', { name: 'Output tree' })).toHaveTextContent('output-tree-body')
    expect(screen.queryByRole('button', { name: 'Input' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Pair phone' })).toBeInTheDocument()
  })

  it('a paired phone never shows desktop-only pairing', async () => {
    setMobile(false)
    await renderSort(false)
    await screen.findByTestId('card')
    expect(screen.queryByRole('button', { name: 'Pair phone' })).toBeNull()
  })
})
