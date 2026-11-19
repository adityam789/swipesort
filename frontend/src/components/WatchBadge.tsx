import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export const WATCH_POLL_MS = 10_000

/**
 * Watch mode's pending count (SPEC §18): files a watched folder couldn't auto-sort. Clicking
 * opens that folder's watch session in the normal swipe UI. Desktop only (the watch endpoints
 * are loopback-only), hidden while watch mode is off.
 */
export function WatchBadge({ onOpen, className = '' }: { onOpen: (sessionId: number) => void; className?: string }) {
  const { data } = useQuery({ queryKey: ['watch-status'], queryFn: api.watchStatus, refetchInterval: WATCH_POLL_MS, retry: false })
  if (!data || !data.enabled) return null
  const target = data.folders.find((f) => f.pending > 0 && f.session_id !== null) ?? data.folders.find((f) => f.session_id !== null)
  const n = data.pending_total
  return (
    <button
      type="button"
      data-testid="watch-badge"
      disabled={!target}
      onClick={() => target?.session_id != null && onOpen(target.session_id)}
      title={n ? `${n} file(s) from watched folders are waiting for you` : 'Watched folders: nothing pending'}
      className={`inline-flex items-center gap-1.5 ${className}`}
    >
      <span aria-hidden>👁</span>
      <span>Pending</span>
      <span className={`min-w-5 rounded-full px-1.5 text-xs font-semibold ${n ? 'bg-amber-500 text-white' : 'bg-stone-200 text-stone-600 dark:bg-stone-800 dark:text-stone-300'}`}>
        {n}
      </span>
    </button>
  )
}
