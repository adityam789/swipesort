import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, prefetchPreview } from '../api/client'
import type { Direction, Edge, FolderOut } from '../api/types'
import { CardView } from '../cards/CardView'
import { Preview } from '../cards/Preview'
import { FolderPicker } from '../components/FolderPicker'
import { ToastHost } from '../components/Toast'
import { useFling } from '../gestures/useFling'
import { useSession } from '../store/session'
import { InputTree } from '../trees/InputTree'
import { OutputTree } from '../trees/OutputTree'
import { Summary } from './Summary'

type Drawer = 'input' | 'output' | null

const DEFER_EDGE: Edge = { label: 'Later', action: 'defer' }

export function Sort({ sessionId, onNew }: { sessionId: number; onNew: () => void }) {
  const qc = useQueryClient()
  const queue = useSession((s) => s.queue)
  const exhausted = useSession((s) => s.exhausted)
  const fetching = useSession((s) => s.fetching)
  const progress = useSession((s) => s.progress)
  const undoCount = useSession((s) => s.undoStack.length)
  const pickerOpen = useSession((s) => s.pickerOpen)
  const previewOpen = useSession((s) => s.previewOpen)
  const lastFolder = useSession((s) => s.lastFolder)
  const treeVersion = useSession((s) => s.treeVersion)
  const flippedIds = useSession((s) => s.flippedIds)
  const { decide, flip, undo, pickFolder, openPicker, closePicker, togglePreview, refresh } = useSession.getState()
  const [drawer, setDrawer] = useState<Drawer>(null)

  const card = queue[0]

  const analyzing = !card && progress.processed < progress.scanned

  // Poll session progress + cards: every ~1s while analyzing, relaxed otherwise (new cards stream in).
  useEffect(() => {
    const t = setInterval(() => {
      if (analyzing || useSession.getState().queue.length < 5) void refresh()
    }, analyzing ? 1000 : 3000)
    return () => clearInterval(t)
  }, [analyzing, refresh])

  // Warm previews for the next two cards so they render instantly after a fling.
  const next1 = queue[1]?.file.id
  const next2 = queue[2]?.file.id
  useEffect(() => {
    if (next1 !== undefined) prefetchPreview(next1)
    if (next2 !== undefined) prefetchPreview(next2)
  }, [next1, next2])

  const { data: folders = [] } = useQuery({
    queryKey: ['folders', sessionId, treeVersion],
    queryFn: () => api.getFolders(sessionId),
    enabled: pickerOpen,
  })

  const act = (edge: Edge) => {
    if (!card) return
    if (edge.action === 'picker') openPicker()
    else if (edge.action === 'flip') flip(card.id)
    else decide(card, edge)
  }
  const fling = (dir: Direction) => {
    const edge = card?.edges[dir]
    if (edge) act(edge)
  }

  const { bind, x, y, rotate, aimed } = useFling({
    edges: card?.edges ?? {},
    enabled: !!card && !pickerOpen,
    onFling: fling,
    keys: card?.keys,
    onKey: act,
    onUndo: () => void undo(),
    onDefer: () => {
      if (!card) return
      const edge = Object.values(card.edges).find((e) => e?.action === 'defer')
      decide(card, edge ?? DEFER_EDGE)
    },
    onPreview: togglePreview,
    onPicker: openPicker,
  })

  useEffect(() => {
    if (!previewOpen) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && togglePreview()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [previewOpen, togglePreview])

  const createFolder = async (parentId: number | null, name: string): Promise<FolderOut> => {
    const f = await api.createFolder({ session_id: sessionId, parent_id: parentId, name })
    void qc.invalidateQueries({ queryKey: ['folders', sessionId] })
    return f
  }

  const done = !card && exhausted && !fetching && !analyzing
  if (done) return <><Summary onNew={onNew} /><ToastHost /></>

  const total = progress.decided + progress.remaining
  const pct = total > 0 ? Math.round((progress.decided / total) * 100) : 0

  const asideBase = 'flex-col border-stone-200 bg-stone-50 dark:border-stone-800 dark:bg-stone-950'
  const aside = (side: Exclude<Drawer, null>) =>
    drawer === side
      ? `fixed inset-x-0 bottom-0 z-30 flex h-[60vh] rounded-t-2xl border-t shadow-2xl md:static md:z-auto md:h-auto md:w-72 md:rounded-none md:border-t-0 md:shadow-none ${asideBase} ${side === 'input' ? 'md:border-r' : 'md:border-l'}`
      : `hidden md:flex md:w-72 ${asideBase} ${side === 'input' ? 'md:border-r' : 'md:border-l'}`

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-stone-200 px-3 py-2 dark:border-stone-800">
        <button type="button" onClick={() => setDrawer(drawer === 'input' ? null : 'input')} className="rounded-lg px-2 py-1 text-sm hover:bg-stone-200 md:hidden dark:hover:bg-stone-800">
          Input
        </button>
        <div className="min-w-0 flex-1">
          <div className="h-1.5 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800">
            <div className="h-full bg-emerald-500 transition-all" style={{ width: `${pct}%` }} />
          </div>
          <p className="mt-1 text-xs text-stone-500">
            {progress.decided} done · {progress.remaining} left · <span data-testid="ready-ahead" title="Cards precomputed and ready">
              {queue.length} ready ahead
            </span>
          </p>
        </div>
        <button
          type="button"
          disabled={undoCount === 0}
          onClick={() => void undo()}
          title="Undo (Z / Ctrl+Z / Cmd+Z)"
          className="rounded-lg border border-stone-300 px-3 py-1 text-sm hover:bg-stone-200 disabled:opacity-30 dark:border-stone-700 dark:hover:bg-stone-800"
        >
          Undo{undoCount ? ` (${undoCount})` : ''}
        </button>
        <button type="button" onClick={() => setDrawer(drawer === 'output' ? null : 'output')} className="rounded-lg px-2 py-1 text-sm hover:bg-stone-200 md:hidden dark:hover:bg-stone-800">
          Output
        </button>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className={aside('input')} aria-label="Input tree">
          <h2 className="px-3 pt-3 text-xs font-semibold uppercase tracking-wide text-stone-400">Input</h2>
          <InputTree sessionId={sessionId} />
        </aside>

        <main className="min-w-0 flex-1 overflow-hidden p-2 md:p-6">
          {card ? (
            <CardView key={card.id} card={card} aimed={aimed} bind={bind} x={x} y={y} rotate={rotate} onEdgeClick={fling} flipped={flippedIds.has(card.id)} />
          ) : (
            <div data-testid="waiting" className="flex h-full items-center justify-center text-stone-400">
              {analyzing ? `Analyzing files… ${progress.processed}/${progress.scanned}` : 'Waiting for cards…'}
            </div>
          )}
        </main>

        <aside className={aside('output')} aria-label="Output tree">
          <h2 className="px-3 pt-3 text-xs font-semibold uppercase tracking-wide text-stone-400">Output</h2>
          <OutputTree sessionId={sessionId} />
        </aside>
      </div>

      {previewOpen && card && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-4 md:p-10" onClick={togglePreview}>
          <div className="h-full w-full max-w-4xl rounded-2xl bg-white p-3 dark:bg-stone-900">
            <Preview fileId={card.file.id} ext={card.file.ext} large />
          </div>
        </div>
      )}

      {pickerOpen && (
        <FolderPicker folders={folders} initialId={lastFolder?.id} onClose={closePicker} onCreate={createFolder} onPick={pickFolder} />
      )}
      <ToastHost />
    </div>
  )
}
