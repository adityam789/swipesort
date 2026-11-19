import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { api } from '../api/client'
import type { SessionOut } from '../api/types'

const POLL_MS = 3000

const baseName = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() ?? p

/**
 * Sessions running in this daemon (created on the desktop). A phone joins one here; with
 * `autoJoin`, a single running session is joined right away.
 */
export function SessionPicker({
  onPick,
  autoJoin = false,
  emptyHint,
  title,
}: {
  onPick: (s: SessionOut) => void
  autoJoin?: boolean
  emptyHint?: string
  title?: string
}) {
  const { data, isLoading, error } = useQuery({ queryKey: ['sessions'], queryFn: api.listSessions, refetchInterval: POLL_MS })
  const live = (data ?? []).filter((s) => s.live !== false && s.status === 'active')
  const joined = useRef(false)

  useEffect(() => {
    if (autoJoin && !joined.current && live.length === 1) {
      joined.current = true
      onPick(live[0])
    }
  }, [autoJoin, live, onPick])

  if (isLoading) return <p className="text-sm text-stone-400">Loading sessions…</p>
  if (error) return <p className="text-sm text-red-600">{error instanceof Error ? error.message : 'Could not load sessions'}</p>
  if (live.length === 0) return emptyHint ? <p className="text-sm text-stone-500" data-testid="no-sessions">{emptyHint}</p> : null
  return (
    <section className="space-y-2">
    {title && <h2 className="text-xs font-semibold uppercase tracking-wide text-stone-400">{title}</h2>}
    <ul className="space-y-2" aria-label="Sessions">
      {live.map((s) => {
        const total = s.progress.decided + s.progress.remaining
        return (
          <li key={s.id}>
            <button
              type="button"
              onClick={() => onPick(s)}
              className="flex min-h-14 w-full items-center gap-3 rounded-xl border border-stone-200 bg-white px-4 py-3 text-left hover:border-emerald-400 dark:border-stone-800 dark:bg-stone-900"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">
                  {baseName(s.input_root)} → {baseName(s.output_root)}
                  {s.kind === 'watch' && <span className="ml-2 rounded bg-amber-100 px-1.5 text-xs font-normal text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">watched</span>}
                </span>
                <span className="block text-xs text-stone-500">
                  {s.progress.decided}/{total} sorted · {s.progress.remaining} left
                </span>
              </span>
              <span aria-hidden className="text-emerald-600">
                ›
              </span>
            </button>
          </li>
        )
      })}
    </ul>
    </section>
  )
}
