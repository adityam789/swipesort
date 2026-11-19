import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import { api, pairQrUrl } from '../api/client'
import type { PairStart } from '../api/types'
import { BottomSheet } from './BottomSheet'

const DEVICES_POLL_MS = 3000

/** Seconds left until `deadline` (ms epoch), ticking once a second. */
function useSecondsLeft(deadline: number | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (deadline === null) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [deadline])
  return deadline === null ? 0 : Math.max(0, Math.ceil((deadline - now) / 1000))
}

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`

function FirewallNote() {
  return (
    <p className="text-xs text-stone-500 dark:text-stone-400">
      <b>Windows:</b> the first time SwipeSort listens on your network, Windows Firewall asks whether to allow it. Allow it on{' '}
      <b>private</b> networks only. If the phone can't connect, check that both devices are on the same Wi-Fi and that the
      firewall allows Python/SwipeSort.
    </p>
  )
}

function LocalOnly() {
  return (
    <div className="space-y-3 text-sm" data-testid="pair-local-only">
      <p>
        SwipeSort is running in <b>localhost-only mode</b>: only this computer can reach it, so no firewall prompt is needed.
      </p>
      <p>To sort from your phone, restart the daemon in LAN mode:</p>
      <pre className="rounded-lg bg-stone-100 px-3 py-2 font-mono text-xs dark:bg-stone-800">swipesort serve --lan</pre>
      <p className="text-xs text-stone-500 dark:text-stone-400">
        (or set <code>server.lan = true</code> in <code>~/.swipesort/config.toml</code>). Your files never leave this computer: the
        phone only shows cards and previews over your local network.
      </p>
      <FirewallNote />
    </div>
  )
}

function Devices() {
  const qc = useQueryClient()
  const { data: devices = [] } = useQuery({ queryKey: ['devices'], queryFn: api.devices, refetchInterval: DEVICES_POLL_MS })
  if (devices.length === 0) return <p className="text-xs text-stone-400">No paired devices yet.</p>
  return (
    <ul className="divide-y divide-stone-200 dark:divide-stone-800" aria-label="Paired devices">
      {devices.map((d) => (
        <li key={d.id} className="flex items-center gap-3 py-2 text-sm">
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium">{d.name}</p>
            <p className="truncate text-xs text-stone-500">last seen {new Date(d.last_seen).toLocaleString()}</p>
          </div>
          <button
            type="button"
            onClick={() => void api.revokeDevice(d.id).then(() => qc.invalidateQueries({ queryKey: ['devices'] }))}
            className="min-h-11 rounded-lg border border-red-300 px-3 text-xs font-medium text-red-700 hover:bg-red-50 md:min-h-0 md:py-1 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950"
          >
            Revoke
          </button>
        </li>
      ))}
    </ul>
  )
}

/** Desktop: pair a phone (QR + URL + countdown), list and revoke paired devices. */
export function PairDialog({ onClose }: { onClose: () => void }) {
  const { data: status } = useQuery({ queryKey: ['pair-status'], queryFn: api.pairStatus })
  const [start, setStart] = useState<(PairStart & { deadline: number }) | null>(null)
  const [error, setError] = useState<string | null>(null)
  const lan = status?.lan ?? false

  const newCode = useCallback(() => {
    api
      .pairStart()
      // Count down from the server's TTL on the local clock (immune to clock skew).
      .then((p) => {
        setError(null)
        setStart({ ...p, deadline: Date.now() + p.expires_in * 1000 })
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Could not create a pairing code'))
  }, [])

  useEffect(() => {
    if (lan) newCode()
  }, [lan, newCode])

  const ticking = useSecondsLeft(start?.deadline ?? null)
  const left = Math.min(start?.expires_in ?? 0, ticking) // `now` may predate the code
  const expired = start !== null && left <= 0

  return (
    <BottomSheet label="Pair phone" title="Pair a phone" onClose={onClose} testId="pair-dialog">
      <div className="space-y-4 overflow-y-auto p-4">
        {!status ? (
          <p className="text-sm text-stone-400">Loading…</p>
        ) : !lan ? (
          <LocalOnly />
        ) : (
          <>
            <p className="text-sm">Scan this with your phone's camera. Both devices must be on the same network.</p>
            {error && <p className="text-sm text-red-600">{error}</p>}
            {start && (
              <div className="flex flex-col items-center gap-2">
                <img
                  src={pairQrUrl(start.code)}
                  alt={`Pairing QR code for ${start.url}`}
                  width={220}
                  height={220}
                  className={`rounded-lg bg-white p-1 ${expired ? 'opacity-20' : ''}`}
                />
                <p className="break-all text-center font-mono text-xs text-stone-500" data-testid="pair-url">
                  {start.url}
                </p>
                <p className="text-sm">
                  Code <b className="font-mono tracking-widest">{start.code}</b>{' '}
                  <span className="text-stone-500" data-testid="pair-expiry">
                    {expired ? '· expired' : `· expires in ${mmss(left)}`}
                  </span>
                </p>
                {expired && (
                  <button type="button" onClick={newCode} className="min-h-11 rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white">
                    New code
                  </button>
                )}
                {start.lan_ip.startsWith('127.') && (
                  <p className="text-xs text-amber-600">No network address found: connect this computer to Wi-Fi.</p>
                )}
              </div>
            )}
            <FirewallNote />
          </>
        )}
        <section className="space-y-1">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-400">Paired devices</h3>
          <Devices />
        </section>
        <div className="flex justify-end">
          <button type="button" onClick={onClose} className="min-h-11 rounded-lg px-3 text-sm md:min-h-0 md:py-1.5">
            Done
          </button>
        </div>
      </div>
    </BottomSheet>
  )
}

export function PairButton({ className = '' }: { className?: string }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className={className} title="Sort from your phone">
        Pair phone
      </button>
      {open && <PairDialog onClose={() => setOpen(false)} />}
    </>
  )
}
