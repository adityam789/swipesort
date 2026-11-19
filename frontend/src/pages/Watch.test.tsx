import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { WatchActivity, WatchConfig, WatchStatus } from '../api/types'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      getWatch: vi.fn(),
      putWatch: vi.fn(),
      watchStatus: vi.fn(),
      watchActivity: vi.fn(),
      undoDecision: vi.fn(),
      browse: vi.fn(() => Promise.resolve({ path: '/', parent: null, entries: [] })),
    },
  }
})

import { ApiError, api } from '../api/client'
import { WatchBadge } from '../components/WatchBadge'
import { Watch } from './Watch'

const cfg = (over: Partial<WatchConfig> = {}): WatchConfig => ({
  enabled: true,
  running: true,
  folders: [{ path: '/Users/me/Downloads', output: '/Users/me/Sorted', enabled: true, session_id: 7, running: true, pending: 3, processing: 0, error: '' }],
  settle_seconds: 5,
  auto_move_threshold: 0.97,
  auto_move: true,
  notify: true,
  auto_trash_exact_dupes: false,
  ignore: ['*.crdownload', '*.part', '*.download', '*.tmp', '*.partial', '*.opdownload'],
  digest_days: 7,
  safety: { decisions: 12, min_decisions_for_auto: 50, auto_unlocked: false },
  ...over,
})

const activity: WatchActivity = {
  days: 7,
  auto_moved: 2,
  auto_trashed: 0,
  pending_total: 3,
  items: [
    { decision_id: 41, session_id: 7, file_id: 1, name: 'invoice_0412.pdf', action: 'move', src: '/Users/me/Downloads/invoice_0412.pdf', dst: '/Users/me/Sorted/Finance/Receipts/invoice_0412.pdf', dest_rel: 'Finance/Receipts', confidence: 0.98, ts: '2026-11-18T10:00:00Z', undone: false, can_undo: true, undo_blocked: '' },
    { decision_id: 42, session_id: 7, file_id: 2, name: 'moved-again.pdf', action: 'move', src: '/Users/me/Downloads/moved-again.pdf', dst: '/Users/me/Sorted/Work/moved-again.pdf', dest_rel: 'Work', confidence: 0.99, ts: '2026-11-18T11:00:00Z', undone: false, can_undo: false, undo_blocked: 'moved-again.pdf was moved again after this' },
  ],
}

const wrap = (node: ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>)

beforeEach(() => {
  vi.mocked(api.getWatch).mockResolvedValue(cfg())
  vi.mocked(api.watchActivity).mockResolvedValue(activity)
  vi.mocked(api.putWatch).mockReset()
  vi.mocked(api.undoDecision).mockReset()
})

describe('Watch page', () => {
  it('lists watched folders, thresholds and the safety progress', async () => {
    wrap(<Watch onBack={() => {}} onOpenSession={() => {}} />)
    expect(await screen.findByText('Downloads → Sorted')).toBeInTheDocument()
    expect(screen.getByTestId('safety')).toHaveTextContent('Auto-move unlocks after 50 decisions: 12/50')
    expect(screen.getByTestId('thresholds')).toHaveTextContent('≥ 97% confidence')
    expect(screen.getByTestId('thresholds')).toHaveTextContent('*.crdownload')
    expect(screen.getByRole('switch', { name: 'Watch folders for new files' })).toBeChecked()
  })

  it('shows auto-move as active once unlocked', async () => {
    vi.mocked(api.getWatch).mockResolvedValue(cfg({ safety: { decisions: 80, min_decisions_for_auto: 50, auto_unlocked: true } }))
    wrap(<Watch onBack={() => {}} onOpenSession={() => {}} />)
    expect(await screen.findByTestId('safety')).toHaveTextContent('Auto-move is active for files at ≥ 97% confidence')
  })

  it('opens the watch session to sort pending files', async () => {
    const open = vi.fn()
    wrap(<Watch onBack={() => {}} onOpenSession={open} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Sort 3 pending' }))
    expect(open).toHaveBeenCalledWith(7)
  })

  it('adds and removes watched folders, toggles settings', async () => {
    vi.mocked(api.putWatch).mockResolvedValue(cfg())
    wrap(<Watch onBack={() => {}} onOpenSession={() => {}} />)
    await screen.findByText('Downloads → Sorted')
    fireEvent.change(screen.getByLabelText('Watch this folder'), { target: { value: '/Users/me/Desktop' } })
    fireEvent.change(screen.getByLabelText('Sort its files into'), { target: { value: '/Users/me/Sorted' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add watched folder' }))
    await waitFor(() =>
      expect(api.putWatch).toHaveBeenCalledWith({
        folders: [
          { path: '/Users/me/Downloads', output: '/Users/me/Sorted', enabled: true },
          { path: '/Users/me/Desktop', output: '/Users/me/Sorted', enabled: true },
        ],
      }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Stop watching /Users/me/Downloads' }))
    await waitFor(() => expect(api.putWatch).toHaveBeenLastCalledWith({ folders: [] }))
    fireEvent.click(screen.getByRole('switch', { name: 'Move high-confidence files automatically' }))
    await waitFor(() => expect(api.putWatch).toHaveBeenLastCalledWith({ auto_move: false }))
  })

  it('shows a save error from the daemon', async () => {
    vi.mocked(api.putWatch).mockRejectedValue(new ApiError(400, 'not a directory: /nope'))
    wrap(<Watch onBack={() => {}} onOpenSession={() => {}} />)
    fireEvent.click(await screen.findByRole('switch', { name: 'Watch folders for new files' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('not a directory: /nope')
  })
})

describe('Watch activity digest', () => {
  it('lists auto-sorted items with per-item undo', async () => {
    vi.mocked(api.undoDecision).mockResolvedValue({ undone_ops: [9], progress: { scanned: 1, processed: 1, ready: 1, decided: 0, remaining: 1 } })
    wrap(<Watch onBack={() => {}} onOpenSession={() => {}} />)
    expect(await screen.findByText('2 auto-sorted in the last 7 days · 3 pending')).toBeInTheDocument()
    const row = screen.getByTestId('activity-41')
    expect(row).toHaveTextContent('→ Finance/Receipts')
    expect(row).toHaveTextContent('98%')
    fireEvent.click(within(row).getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(api.undoDecision).toHaveBeenCalledWith(41))
  })

  it('disables undo when a later change depends on the move', async () => {
    wrap(<Watch onBack={() => {}} onOpenSession={() => {}} />)
    const row = await screen.findByTestId('activity-42')
    const btn = within(row).getByRole('button', { name: 'Undo' })
    expect(btn).toBeDisabled()
    expect(btn).toHaveAttribute('title', 'moved-again.pdf was moved again after this')
  })

  it('highlights the item a notification linked to', async () => {
    wrap(<Watch onBack={() => {}} onOpenSession={() => {}} highlight={41} />)
    expect(await screen.findByTestId('activity-41')).toHaveAttribute('data-highlight', 'true')
    expect(screen.getByTestId('activity-42')).not.toHaveAttribute('data-highlight')
  })

  it('shows a refused undo', async () => {
    vi.mocked(api.undoDecision).mockRejectedValue(new ApiError(409, 'invoice_0412.pdf was moved again after this'))
    wrap(<Watch onBack={() => {}} onOpenSession={() => {}} />)
    fireEvent.click(within(await screen.findByTestId('activity-41')).getByRole('button', { name: 'Undo' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('moved again')
  })
})

describe('WatchBadge', () => {
  const status = (over: Partial<WatchStatus> = {}): WatchStatus => ({
    enabled: true,
    running: true,
    pending_total: 3,
    folders: cfg().folders,
    safety: cfg().safety,
    ...over,
  })

  it('shows the pending count and opens the watch session', async () => {
    vi.mocked(api.watchStatus).mockResolvedValue(status())
    const open = vi.fn()
    wrap(<WatchBadge onOpen={open} />)
    const badge = await screen.findByTestId('watch-badge')
    expect(badge).toHaveTextContent('Pending3')
    fireEvent.click(badge)
    expect(open).toHaveBeenCalledWith(7)
  })

  it('is hidden while watch mode is off or unavailable (phone)', async () => {
    vi.mocked(api.watchStatus).mockResolvedValue(status({ enabled: false }))
    const { unmount } = wrap(<WatchBadge onOpen={() => {}} />)
    await waitFor(() => expect(api.watchStatus).toHaveBeenCalled())
    expect(screen.queryByTestId('watch-badge')).toBeNull()
    unmount()
    vi.mocked(api.watchStatus).mockRejectedValue(new ApiError(403, 'only available on the computer running SwipeSort'))
    wrap(<WatchBadge onOpen={() => {}} />)
    await waitFor(() => expect(api.watchStatus).toHaveBeenCalledTimes(2))
    expect(screen.queryByTestId('watch-badge')).toBeNull()
  })
})
