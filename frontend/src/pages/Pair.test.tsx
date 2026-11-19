import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return { ...actual, api: { pair: vi.fn() } }
})

import { ApiError, api } from '../api/client'
import { deviceName } from '../lib/device'
import { Pair } from './Pair'

const pair = vi.mocked(api.pair)

beforeEach(() => vi.resetAllMocks())

describe('Pair page', () => {
  it('redeems the code from the QR once and hands over', async () => {
    pair.mockResolvedValue({ device_id: 1, token: 't', expires_at: null })
    const onPaired = vi.fn()
    render(<Pair initialCode="ABCD2345" onPaired={onPaired} />)
    expect(screen.getByTestId('pairing')).toBeInTheDocument()
    await waitFor(() => expect(onPaired).toHaveBeenCalledTimes(1))
    expect(pair).toHaveBeenCalledTimes(1)
    expect(pair.mock.calls[0][0]).toBe('ABCD2345')
  })

  it('explains an expired code and lets you type a new one', async () => {
    pair.mockRejectedValueOnce(new ApiError(403, 'invalid or expired pairing code'))
    pair.mockResolvedValueOnce({ device_id: 2, token: 't', expires_at: null })
    const onPaired = vi.fn()
    render(<Pair initialCode="OLDCODE2" onPaired={onPaired} />)
    expect(await screen.findByRole('alert')).toHaveTextContent(/invalid or expired/)
    const user = userEvent.setup()
    const input = screen.getByLabelText('Pairing code')
    await user.clear(input)
    await user.type(input, 'NEWCODE3')
    await user.click(screen.getByRole('button', { name: 'Pair' }))
    await waitFor(() => expect(onPaired).toHaveBeenCalled())
    expect(pair.mock.calls[1][0]).toBe('NEWCODE3')
  })

  it('shows a rate-limit message', async () => {
    pair.mockRejectedValue(new ApiError(429, 'too many'))
    render(<Pair onPaired={() => undefined} />)
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Pairing code'), 'X')
    await user.click(screen.getByRole('button', { name: 'Pair' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Too many attempts/)
  })

  it('names the device from its user agent', () => {
    expect(deviceName('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)')).toBe('iPhone')
    expect(deviceName('Mozilla/5.0 (Linux; Android 15; Pixel 9) Mobile Safari')).toBe('Android phone')
    expect(deviceName('curl/8')).toBe('Phone')
  })
})
