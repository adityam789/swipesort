import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return { ...actual, api: { pairStatus: vi.fn(), pairStart: vi.fn(), devices: vi.fn(), revokeDevice: vi.fn() } }
})

import { api } from '../api/client'
import { PairDialog } from './PairDialog'

const ui = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PairDialog onClose={() => undefined} />
    </QueryClientProvider>,
  )

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.devices).mockResolvedValue([])
})

describe('PairDialog', () => {
  it('localhost-only mode explains how to enable LAN and the firewall prompt', async () => {
    vi.mocked(api.pairStatus).mockResolvedValue({ local: true, paired: true, lan: false, device_id: null })
    ui()
    const box = await screen.findByTestId('pair-local-only')
    expect(box).toHaveTextContent('swipesort serve --lan')
    expect(box).toHaveTextContent(/Windows Firewall/)
    expect(api.pairStart).not.toHaveBeenCalled()
  })

  it('LAN mode shows the QR, URL and countdown; devices can be revoked', async () => {
    vi.mocked(api.pairStatus).mockResolvedValue({ local: true, paired: true, lan: true, device_id: null })
    vi.mocked(api.pairStart).mockResolvedValue({
      code: 'ABCD2345', url: 'http://192.168.1.10:8765/pair?code=ABCD2345', lan: true, lan_ip: '192.168.1.10',
      expires_at: '2030-01-01T00:00:00Z', expires_in: 300,
    })
    vi.mocked(api.devices).mockResolvedValue([
      { id: 7, name: 'iPhone', user_agent: 'x', created_at: '2026-01-01T00:00:00Z', last_seen: '2026-01-01T00:00:00Z', expires_at: null },
    ])
    vi.mocked(api.revokeDevice).mockResolvedValue(undefined)
    ui()
    const img = await screen.findByRole('img', { name: /Pairing QR code/ })
    expect(img).toHaveAttribute('src', '/api/pair/qr.svg?code=ABCD2345')
    expect(screen.getByTestId('pair-url')).toHaveTextContent('http://192.168.1.10:8765/pair?code=ABCD2345')
    expect(screen.getByTestId('pair-expiry')).toHaveTextContent('expires in 5:00')
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Revoke' }))
    await waitFor(() => expect(api.revokeDevice).toHaveBeenCalledWith(7))
  })
})
