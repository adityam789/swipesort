import { useState } from 'react'
import { api } from '../api/client'
import type { SessionOut } from '../api/types'
import { PairButton } from '../components/PairDialog'
import { PathField } from '../components/PathField'
import { SessionPicker } from '../components/SessionPicker'
import { WatchBadge } from '../components/WatchBadge'

const headerBtn = 'min-h-11 rounded-lg border border-stone-300 px-3 text-sm hover:bg-stone-100 disabled:opacity-50 dark:border-stone-700 dark:hover:bg-stone-800'

export function Setup({
  onStart,
  onWatch,
  onOpenSession,
}: {
  onStart: (s: SessionOut) => void
  onWatch?: () => void
  onOpenSession?: (id: number) => void
}) {
  const [input, setInput] = useState('')
  const [output, setOutput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      onStart(await api.createSession({ input_root: input.trim(), output_root: output.trim() }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start session')
      setBusy(false)
    }
  }

  return (
    <main className="mx-auto flex min-h-full max-w-xl flex-col justify-center gap-6 px-5 py-10">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <h1 className="text-3xl font-semibold tracking-tight">SwipeSort</h1>
          <p className="mt-1 text-stone-500 dark:text-stone-400">Fling each file to where it belongs.</p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          {onOpenSession && <WatchBadge onOpen={onOpenSession} className={headerBtn} />}
          {onWatch && (
            <button type="button" onClick={onWatch} className={headerBtn}>
              Watch folders
            </button>
          )}
          <PairButton className={headerBtn} />
        </div>
      </div>
      <SessionPicker onPick={onStart} title="Running sessions" />
      <PathField label="Folder to sort (input)" value={input} onChange={setInput} />
      <PathField label="Where sorted files go (output)" value={output} onChange={setOutput} />
      <p className="text-xs text-stone-400">The two may be the same folder to sort in place. Nothing is ever deleted; Trash is undoable.</p>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button
        type="button"
        disabled={busy || !input.trim() || !output.trim()}
        onClick={() => void start()}
        className="self-start rounded-xl bg-emerald-600 px-6 py-2.5 font-medium text-white shadow-sm hover:bg-emerald-700 disabled:opacity-40"
      >
        {busy ? 'Starting…' : 'Start'}
      </button>
    </main>
  )
}
