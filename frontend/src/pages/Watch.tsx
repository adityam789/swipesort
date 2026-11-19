import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import type { WatchActivityItem, WatchConfig, WatchConfigIn } from '../api/types'
import { PathField } from '../components/PathField'
import { WATCH_POLL_MS } from '../components/WatchBadge'

const baseName = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() ?? p
const pct = (x: number) => `${Math.round(x * 100)}%`

function when(ts: string): string {
  const d = new Date(ts.endsWith('Z') || ts.includes('+') ? ts : `${ts}Z`)
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })
}

function Toggle({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="flex min-h-11 items-center justify-between gap-3 text-sm">
      <span>{label}</span>
      <input type="checkbox" role="switch" aria-label={label} checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} className="h-5 w-9 accent-emerald-600" />
    </label>
  )
}

/** "Auto-move unlocks after 50 decisions: 12/50". */
export function SafetyStatus({ cfg }: { cfg: WatchConfig }) {
  const { decisions, min_decisions_for_auto: min, auto_unlocked } = cfg.safety
  if (!cfg.auto_move) return <p data-testid="safety" className="text-sm text-stone-500">Auto-move is off: every new file becomes a card.</p>
  if (auto_unlocked)
    return (
      <p data-testid="safety" className="text-sm text-emerald-700 dark:text-emerald-400">
        Auto-move is active for files at ≥ {pct(cfg.auto_move_threshold)} confidence.
      </p>
    )
  return (
    <div data-testid="safety" className="space-y-1">
      <p className="text-sm text-amber-700 dark:text-amber-400">
        Auto-move unlocks after {min} decisions: {decisions}/{min}
      </p>
      <div className="h-1.5 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800">
        <div className="h-full bg-amber-500" style={{ width: `${Math.min(100, (decisions / Math.max(1, min)) * 100)}%` }} />
      </div>
      <p className="text-xs text-stone-500">Until then every new file becomes a card, so the confidence calibration has data.</p>
    </div>
  )
}

function ActivityRow({ item, highlight, onUndo, busy }: { item: WatchActivityItem; highlight: boolean; onUndo: () => void; busy: boolean }) {
  const dest = item.action === 'trash' ? 'quarantined (duplicate)' : `→ ${item.dest_rel || '(output root)'}`
  return (
    <li
      data-testid={`activity-${item.decision_id}`}
      data-highlight={highlight || undefined}
      className={`flex items-center gap-3 rounded-xl border px-3 py-2 ${highlight ? 'border-amber-400 ring-2 ring-amber-300' : 'border-stone-200 dark:border-stone-800'}`}
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium" title={item.src}>
          {item.name}
        </p>
        <p className="truncate text-xs text-stone-500" title={item.dst}>
          {dest} · {when(item.ts)}
          {item.confidence !== null && ` · ${pct(item.confidence)}`}
        </p>
      </div>
      {item.undone ? (
        <span className="text-xs text-stone-400">Undone</span>
      ) : (
        <button
          type="button"
          disabled={!item.can_undo || busy}
          title={item.can_undo ? `Put ${item.name} back` : item.undo_blocked}
          onClick={onUndo}
          className="min-h-11 rounded-lg border border-stone-300 px-3 text-sm hover:bg-stone-100 disabled:opacity-40 md:min-h-0 md:py-1 dark:border-stone-700 dark:hover:bg-stone-800"
        >
          Undo
        </button>
      )}
    </li>
  )
}

/** Watch settings (folders, toggles, thresholds, safety) and the weekly digest with Undo. */
export function Watch({ onBack, onOpenSession, highlight = null }: { onBack: () => void; onOpenSession: (id: number) => void; highlight?: number | null }) {
  const qc = useQueryClient()
  const cfgQ = useQuery({ queryKey: ['watch'], queryFn: api.getWatch, refetchInterval: WATCH_POLL_MS })
  const cfg = cfgQ.data
  const actQ = useQuery({
    queryKey: ['watch-activity'],
    queryFn: () => api.watchActivity(cfg?.digest_days),
    refetchInterval: WATCH_POLL_MS,
    enabled: !!cfg,
  })
  const [newPath, setNewPath] = useState('')
  const [newOutput, setNewOutput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['watch'] })
    void qc.invalidateQueries({ queryKey: ['watch-activity'] })
    void qc.invalidateQueries({ queryKey: ['watch-status'] })
  }
  const save = async (body: WatchConfigIn) => {
    setBusy(true)
    setError(null)
    try {
      qc.setQueryData(['watch'], await api.putWatch(body))
      refresh()
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save')
      return false
    } finally {
      setBusy(false)
    }
  }
  const folders = (cfg?.folders ?? []).map(({ path, output, enabled }) => ({ path, output, enabled }))
  const add = async () => {
    if (await save({ folders: [...folders, { path: newPath.trim(), output: newOutput.trim(), enabled: true }] })) {
      setNewPath('')
      setNewOutput('')
    }
  }
  const undo = async (id: number) => {
    setBusy(true)
    setError(null)
    try {
      await api.undoDecision(id)
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Undo failed')
    } finally {
      setBusy(false)
    }
  }

  const act = actQ.data
  const card = 'space-y-3 rounded-2xl border border-stone-200 bg-white p-4 dark:border-stone-800 dark:bg-stone-900'
  return (
    <main className="mx-auto flex min-h-full max-w-2xl flex-col gap-5 px-5 py-8">
      <div className="flex items-center gap-3">
        <button type="button" onClick={onBack} className="min-h-11 rounded-lg px-2 text-sm hover:bg-stone-100 dark:hover:bg-stone-800" aria-label="Back">
          ←
        </button>
        <h1 className="flex-1 text-2xl font-semibold tracking-tight">Watched folders</h1>
      </div>
      {cfgQ.isError && <p className="text-sm text-red-600">Watch settings are only available on the computer running SwipeSort.</p>}
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      {cfg && (
        <>
          <section className={card} aria-label="Watch settings">
            <Toggle label="Watch folders for new files" checked={cfg.enabled} disabled={busy} onChange={(v) => void save({ enabled: v })} />
            <ul className="space-y-2" aria-label="Watched folders">
              {cfg.folders.length === 0 && <li className="text-sm text-stone-500">No watched folders yet.</li>}
              {cfg.folders.map((f, i) => (
                <li key={`${f.path}|${f.output}`} className="flex items-center gap-2 rounded-xl bg-stone-50 px-3 py-2 dark:bg-stone-950">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium" title={`${f.path} → ${f.output}`}>
                      {baseName(f.path)} → {baseName(f.output)}
                    </p>
                    <p className="truncate text-xs text-stone-500">
                      {f.error ? <span className="text-red-600">{f.error}</span> : f.running ? 'watching' : cfg.enabled ? 'not running' : 'paused'}
                      {f.processing > 0 && ` · ${f.processing} settling`}
                    </p>
                  </div>
                  {f.session_id !== null && (
                    <button type="button" onClick={() => onOpenSession(f.session_id as number)} className="min-h-11 rounded-lg px-2 text-sm text-emerald-700 hover:bg-stone-100 md:min-h-0 md:py-1 dark:text-emerald-400 dark:hover:bg-stone-800">
                      Sort {f.pending} pending
                    </button>
                  )}
                  <button
                    type="button"
                    aria-label={`Stop watching ${f.path}`}
                    disabled={busy}
                    onClick={() => void save({ folders: folders.filter((_, j) => j !== i) })}
                    className="min-h-11 rounded-lg px-2 text-sm text-stone-500 hover:bg-stone-100 md:min-h-0 md:py-1 dark:hover:bg-stone-800"
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
            <div className="space-y-2 border-t border-stone-200 pt-3 dark:border-stone-800">
              <PathField label="Watch this folder" value={newPath} onChange={setNewPath} />
              <PathField label="Sort its files into" value={newOutput} onChange={setNewOutput} />
              <button
                type="button"
                disabled={busy || !newPath.trim() || !newOutput.trim()}
                onClick={() => void add()}
                className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-40"
              >
                Add watched folder
              </button>
            </div>
          </section>

          <section className={card} aria-label="Auto-move">
            <Toggle label="Move high-confidence files automatically" checked={cfg.auto_move} disabled={busy} onChange={(v) => void save({ auto_move: v })} />
            <Toggle label="Notify me when a file is sorted" checked={cfg.notify} disabled={busy} onChange={(v) => void save({ notify: v })} />
            <Toggle label="Quarantine exact duplicates of sorted files" checked={cfg.auto_trash_exact_dupes} disabled={busy} onChange={(v) => void save({ auto_trash_exact_dupes: v })} />
            <SafetyStatus cfg={cfg} />
            <p data-testid="thresholds" className="text-xs text-stone-500">
              Auto-move at ≥ {pct(cfg.auto_move_threshold)} confidence · waits {cfg.settle_seconds} s after a download finishes · ignores {cfg.ignore.slice(0, 5).join(', ')}
              {cfg.ignore.length > 5 ? '…' : ''}. Thresholds live in config.toml.
            </p>
          </section>

          <section className={card} aria-label="Activity">
            <h2 className="text-sm font-semibold">
              {act ? `${act.auto_moved} auto-sorted in the last ${act.days} days · ${act.pending_total} pending` : 'Activity'}
            </h2>
            {act && act.items.length === 0 && <p className="text-sm text-stone-500">Nothing was sorted automatically yet.</p>}
            <ul className="space-y-2">
              {act?.items.map((it) => (
                <ActivityRow key={it.decision_id} item={it} highlight={it.decision_id === highlight} busy={busy} onUndo={() => void undo(it.decision_id)} />
              ))}
            </ul>
          </section>
        </>
      )}
    </main>
  )
}
