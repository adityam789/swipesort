import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { BrowseOut, SessionOut } from '../api/types'
import { PairButton } from '../components/PairDialog'
import { SessionPicker } from '../components/SessionPicker'

function BrowseDialog({ initial, onSelect, onClose }: { initial: string; onSelect: (p: string) => void; onClose: () => void }) {
  const [data, setData] = useState<BrowseOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [path, setPath] = useState(initial)

  useEffect(() => {
    let cancelled = false
    api
      .browse(path)
      .then((d) => !cancelled && (setData(d), setError(null)))
      .catch((e: unknown) => !cancelled && setError(e instanceof Error ? e.message : 'Failed to browse'))
    return () => {
      cancelled = true
    }
  }, [path])

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 md:items-center md:p-6"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      onKeyDown={(e) => e.key === 'Escape' && onClose()}
    >
      <div role="dialog" aria-label="Browse folders" className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-t-2xl bg-white shadow-xl md:rounded-2xl dark:bg-stone-900">
        <div className="flex items-center gap-2 border-b border-stone-200 p-3 dark:border-stone-800">
          <button
            type="button"
            disabled={!data?.parent}
            onClick={() => data?.parent && setPath(data.parent)}
            className="rounded-lg px-2 py-1 text-sm disabled:opacity-30"
            aria-label="Parent folder"
          >
            ↑
          </button>
          <span className="min-w-0 flex-1 truncate text-sm font-medium" title={data?.path}>
            {data?.path ?? '…'}
          </span>
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto py-1">
          {error && <li className="px-4 py-2 text-sm text-red-600">{error}</li>}
          {data?.entries
            .filter((e) => e.is_dir)
            .map((e) => (
              <li key={e.path}>
                <button type="button" onClick={() => setPath(e.path)} className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm hover:bg-stone-100 dark:hover:bg-stone-800">
                  <span aria-hidden>📁</span>
                  <span className="truncate">{e.name}</span>
                </button>
              </li>
            ))}
        </ul>
        <div className="flex justify-end gap-2 border-t border-stone-200 p-3 dark:border-stone-800">
          <button type="button" onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm">
            Cancel
          </button>
          <button
            type="button"
            disabled={!data}
            onClick={() => data && onSelect(data.path)}
            className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            Select this folder
          </button>
        </div>
      </div>
    </div>
  )
}

function PathField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  const [browsing, setBrowsing] = useState(false)
  return (
    <div className="space-y-1.5">
      <label className="text-sm font-medium">
        {label}
        <div className="mt-1.5 flex gap-2">
          <input
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder="/path/to/folder"
            spellCheck={false}
            className="min-w-0 flex-1 rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm font-normal outline-none focus:border-emerald-500 dark:border-stone-700 dark:bg-stone-900"
          />
          <button
            type="button"
            onClick={() => setBrowsing(true)}
            className="rounded-lg border border-stone-300 px-3 py-2 text-sm font-normal hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-800"
          >
            Browse…
          </button>
        </div>
      </label>
      {browsing && (
        <BrowseDialog
          initial={value}
          onClose={() => setBrowsing(false)}
          onSelect={(p) => {
            onChange(p)
            setBrowsing(false)
          }}
        />
      )}
    </div>
  )
}

export function Setup({ onStart }: { onStart: (s: SessionOut) => void }) {
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
        <PairButton className="min-h-11 rounded-lg border border-stone-300 px-3 text-sm hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-800" />
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
