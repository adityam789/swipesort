import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SessionOut } from './api/types'

vi.mock('./api/client', async () => {
  const actual = await vi.importActual<typeof import('./api/client')>('./api/client')
  return {
    ...actual,
    api: {
      pairStatus: vi.fn(),
      pair: vi.fn(),
      listSessions: vi.fn(),
      getCards: vi.fn(() => Promise.resolve([])),
      getSession: vi.fn(),
      getFolders: vi.fn(() => Promise.resolve([])),
    },
    fetchPreview: () => Promise.resolve(null),
    prefetchPreview: vi.fn(),
  }
})
vi.mock('./trees/InputTree', () => ({ InputTree: () => null }))
vi.mock('./trees/OutputTree', () => ({ OutputTree: () => null }))

import { Root } from './App'
import { UNAUTHORIZED_EVENT, api } from './api/client'
import { useSession } from './store/session'

const sess: SessionOut = {
  id: 3, input_root: '/Users/me/Downloads', output_root: '/Users/me/Sorted', status: 'active', live: true,
  progress: { scanned: 2, processed: 2, ready: 2, decided: 0, remaining: 2 },
}

const ui = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Root />
    </QueryClientProvider>,
  )

beforeEach(() => {
  vi.mocked(api.getSession).mockResolvedValue(sess)
  useSession.getState().reset()
})
afterEach(() => window.history.replaceState(null, '', '/'))

describe('app routing', () => {
  it('an unpaired phone lands on pairing', async () => {
    vi.mocked(api.pairStatus).mockResolvedValue({ local: false, paired: false, lan: true, device_id: null })
    ui()
    expect(await screen.findByLabelText('Pairing code')).toBeInTheDocument()
  })

  it('/pair?code= redeems the code, then shows the sessions', async () => {
    window.history.replaceState(null, '', '/pair?code=ABCD2345')
    vi.mocked(api.pairStatus).mockResolvedValue({ local: false, paired: true, lan: true, device_id: 1 })
    vi.mocked(api.pair).mockResolvedValue({ device_id: 1, token: 't', expires_at: null })
    vi.mocked(api.listSessions).mockResolvedValue([])
    ui()
    expect(await screen.findByTestId('no-sessions')).toBeInTheDocument()
    expect(api.pair).toHaveBeenCalledWith('ABCD2345', expect.any(String))
    expect(window.location.pathname).toBe('/')
  })

  it('a paired phone joins the single running session, and a revoked one is sent back to pairing', async () => {
    const status = vi.mocked(api.pairStatus)
    status.mockResolvedValue({ local: false, paired: true, lan: true, device_id: 1 })
    vi.mocked(api.listSessions).mockResolvedValue([sess])
    ui()
    await waitFor(() => expect(screen.getByRole('button', { name: /Undo/ })).toBeInTheDocument())
    expect(useSession.getState().sessionId).toBe(3)

    status.mockResolvedValue({ local: false, paired: false, lan: true, device_id: null })
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    expect(await screen.findByLabelText('Pairing code')).toBeInTheDocument()
  })
})
