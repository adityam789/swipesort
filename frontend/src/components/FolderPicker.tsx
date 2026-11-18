import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import type { FolderOut } from '../api/types'
import { filterFolders } from '../lib/fuzzy'

export interface FolderPickerProps {
  folders: FolderOut[]
  onPick: (folder: FolderOut) => void
  onClose: () => void
  /** Create a folder under `parentId` (null = output root); resolves to the new folder. */
  onCreate: (parentId: number | null, name: string) => Promise<FolderOut>
  initialId?: number | null
}

const label = (f: FolderOut) => f.path || '/ (output root)'

export function FolderPicker({ folders, onPick, onClose, onCreate, initialId }: FolderPickerProps) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const listRef = useRef<HTMLUListElement>(null)

  const results = useMemo(() => {
    const list = filterFolders(folders, query)
    if (query.trim() || initialId == null) return list
    // Empty query: put the last-used folder first.
    const first = list.find((f) => f.id === initialId)
    return first ? [first, ...list.filter((f) => f !== first)] : list
  }, [folders, query, initialId])

  const selected: FolderOut | undefined = results[Math.min(active, results.length - 1)]

  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView?.({ block: 'nearest' })
  }, [active, results])

  const submitCreate = async () => {
    const name = newName.trim()
    if (!name || busy) return
    setBusy(true)
    setError(null)
    try {
      onPick(await onCreate(selected?.id ?? null, name))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not create folder')
      setBusy(false)
    }
  }

  const onKeyDown = (e: KeyboardEvent) => {
    e.stopPropagation()
    if (e.key === 'Escape') {
      e.preventDefault()
      if (creating) setCreating(false)
      else onClose()
      return
    }
    if (creating) {
      if (e.key === 'Enter') {
        e.preventDefault()
        void submitCreate()
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (selected) onPick(selected)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 md:items-center md:p-6"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-label="Choose folder"
        onKeyDown={onKeyDown}
        className="flex max-h-[80vh] w-full max-w-lg flex-col overflow-hidden rounded-t-2xl bg-white shadow-xl md:rounded-2xl dark:bg-stone-900"
      >
        <input
          autoFocus
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setActive(0)
          }}
          placeholder="Search folders…"
          aria-label="Search folders"
          className="border-b border-stone-200 bg-transparent px-4 py-3 text-base outline-none dark:border-stone-800"
        />
        <ul ref={listRef} role="listbox" className="min-h-0 flex-1 overflow-y-auto py-1">
          {results.length === 0 && <li className="px-4 py-3 text-sm text-stone-500">No matching folders</li>}
          {results.map((f, i) => (
            <li
              key={f.id}
              role="option"
              aria-selected={f === selected}
              onMouseMove={() => setActive(i)}
              onClick={() => onPick(f)}
              className={`flex cursor-pointer items-baseline justify-between gap-3 px-4 py-2 text-sm ${
                f === selected ? 'bg-emerald-50 dark:bg-emerald-950' : ''
              }`}
            >
              <span className="truncate">{label(f)}</span>
            </li>
          ))}
        </ul>
        <div className="border-t border-stone-200 p-3 dark:border-stone-800">
          {creating ? (
            <div className="space-y-2">
              <p className="text-xs text-stone-500">
                New folder in <b>{selected ? label(selected) : '/ (output root)'}</b>
              </p>
              <input
                autoFocus
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Folder name"
                aria-label="New folder name"
                className="w-full rounded-lg border border-stone-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-emerald-500 dark:border-stone-700"
              />
              {error && <p className="text-xs text-red-600">{error}</p>}
              <p className="text-xs text-stone-400">Enter to create and move · Esc to go back</p>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <span className="text-xs text-stone-400">↑↓ navigate · Enter select · Esc close</span>
              <button
                type="button"
                onClick={() => setCreating(true)}
                className="rounded-lg px-3 py-1.5 text-sm font-medium text-emerald-700 hover:bg-emerald-50 dark:text-emerald-300 dark:hover:bg-emerald-950"
              >
                + New folder
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
