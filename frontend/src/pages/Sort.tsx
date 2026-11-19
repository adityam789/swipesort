import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, prefetchPreview } from '../api/client'
import type { Direction, Edge, FolderOut } from '../api/types'
import { CardView } from '../cards/CardView'
import { DuplicatePanel } from '../cards/DuplicatePanel'
import { Preview } from '../cards/Preview'
import { BottomSheet } from '../components/BottomSheet'
import { FolderPicker } from '../components/FolderPicker'
import { PairButton } from '../components/PairDialog'
import { ToastHost } from '../components/Toast'
import { WatchBadge } from '../components/WatchBadge'
import { useFling } from '../gestures/useFling'
import { useIsMobile } from '../gestures/useIsMobile'
import { useSession } from '../store/session'
import { InputTree } from '../trees/InputTree'
import { OutputTree } from '../trees/OutputTree'
import { Summary } from './Summary'

type Drawer = 'input' | 'output' | null

const DEFER_EDGE: Edge = { label: 'Later', action: 'defer' }

export function Sort({
  sessionId,
  onNew,
  local = true,
  onOpenSession,
}: {
  sessionId: number
  onNew: () => void
  local?: boolean
  onOpenSession?: (id: number) => void
}) {
  const qc = useQueryClient()
  const mobile = useIsMobile()
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
  const detailsOpen = useSession((s) => s.detailsOpen)
  const socketOpen = useSession((s) => s.socketOpen)
  const { decide, flip, undo, pickFolder, openPicker, closePicker, togglePreview, refresh, openDetails, closeDetails } = useSession.getState()
  const [drawer, setDrawer] = useState<Drawer>(null)

  const card = queue[0]

  const analyzing = !card && progress.processed < progress.scanned

  // Poll session progress + cards: every ~1s while analyzing, relaxed otherwise (new cards stream in).
  // Fallback only: while the WebSocket is open the server pushes everything.
  useEffect(() => {
    if (socketOpen) return
    const t = setInterval(() => {
      if (analyzing || useSession.getState().queue.length < 5) void refresh()
    }, analyzing ? 1000 : 3000)
    return () => clearInterval(t)
  }, [analyzing, refresh, socketOpen])

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
    else if (edge.action === 'details') openDetails() // client-side only, no POST
    else decide(card, edge)
  }
  const fling = (dir: Direction) => {
    const edge = card?.edges[dir]
    if (edge) act(edge)
  }

  const { bind, x, y, rotate, aimed } = useFling({
    edges: card?.edges ?? {},
    enabled: !!card && !pickerOpen && !detailsOpen,
    onFling: fling,
    keys: card?.keys,
    onKey: act,
    onUndo: () => void undo(),
    onDefer: () => {
      if (!card || card.proposal || card.taxonomy) return // proposals have no "later"
      const edge = Object.values(card.edges).find((e) => e?.action === 'defer' || e?.action === 'review')
      decide(card, edge ?? DEFER_EDGE)
    },
    onPreview: togglePreview,
    onTap: mobile ? togglePreview : undefined, // no Space key on a phone: tap the card
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

  const asideBase = 'flex w-72 flex-col border-stone-200 bg-stone-50 dark:border-stone-800 dark:bg-stone-950'
  const btn = 'min-h-11 rounded-lg px-3 text-sm hover:bg-stone-200 md:min-h-0 md:py-1 dark:hover:bg-stone-800'
  const trees = {
    input: { label: 'Input tree', title: 'Input', body: <InputTree sessionId={sessionId} /> },
    output: { label: 'Output tree', title: 'Output', body: <OutputTree sessionId={sessionId} /> },
  } as const

  const undoButton = (
    <button
      type="button"
      disabled={undoCount === 0}
      onClick={() => void undo()}
      title="Undo (Z / Ctrl+Z / Cmd+Z)"
      className="min-h-11 shrink-0 rounded-lg border border-stone-300 px-3 text-sm hover:bg-stone-200 disabled:opacity-30 md:min-h-0 md:py-1 dark:border-stone-700 dark:hover:bg-stone-800"
    >
      <span aria-hidden className="mr-1">↶</span>Undo{undoCount ? ` (${undoCount})` : ''}
    </button>
  )

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="border-b border-stone-200 px-2 pt-[env(safe-area-inset-top)] pr-[max(0.5rem,env(safe-area-inset-right))] pl-[max(0.5rem,env(safe-area-inset-left))] md:px-3 md:py-2 dark:border-stone-800">
        <div className={`flex items-center gap-2 md:gap-3 ${mobile ? 'flex-wrap' : ''}`}>
          {mobile && (
            <div className="flex w-full items-center gap-1 pt-1">
              <button type="button" onClick={() => setDrawer('input')} className={btn}>
                Input
              </button>
              <button type="button" onClick={() => setDrawer('output')} className={btn}>
                Output
              </button>
              <div className="flex-1" />
              {undoButton}
            </div>
          )}
          <div className="min-w-0 flex-1 pb-2 md:py-0">
            <div className="h-1.5 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800">
              <div className="h-full bg-emerald-500 transition-all" style={{ width: `${pct}%` }} />
            </div>
            <p className="mt-1 truncate text-xs text-stone-500">
              {progress.decided} done · {progress.remaining} left · <span data-testid="ready-ahead" title="Cards precomputed and ready">
                {queue.length} ready{mobile ? '' : ' ahead'}
              </span>
              {progress.processed < progress.scanned && (
                <span data-testid="analyzing" className="ml-1 text-amber-600 dark:text-amber-400">
                  {' '}· analyzing {progress.processed}/{progress.scanned}
                </span>
              )}
            </p>
          </div>
          {!mobile && local && onOpenSession && <WatchBadge onOpen={onOpenSession} className={btn} />}
          {!mobile && local && <PairButton className={btn} />}
          {!mobile && undoButton}
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {!mobile && (
          <aside className={`${asideBase} border-r`} aria-label={trees.input.label}>
            <h2 className="px-3 pt-3 text-xs font-semibold uppercase tracking-wide text-stone-400">Input</h2>
            {trees.input.body}
          </aside>
        )}

        <main
          data-layout={mobile ? 'mobile' : 'desktop'}
          className={`min-w-0 flex-1 overflow-hidden ${mobile ? 'px-0 pt-1 pb-[env(safe-area-inset-bottom)]' : 'p-6'}`}
        >
          {card ? (
            <CardView key={card.id} card={card} aimed={aimed} bind={bind} x={x} y={y} rotate={rotate} onEdgeClick={fling} flipped={flippedIds.has(card.id)} fullscreen={mobile} />
          ) : (
            <div data-testid="waiting" className="flex h-full items-center justify-center text-stone-400">
              {analyzing ? `Analyzing files… ${progress.processed}/${progress.scanned}` : 'Waiting for cards…'}
            </div>
          )}
        </main>

        {!mobile && (
          <aside className={`${asideBase} border-l`} aria-label={trees.output.label}>
            <h2 className="px-3 pt-3 text-xs font-semibold uppercase tracking-wide text-stone-400">Output</h2>
            {trees.output.body}
          </aside>
        )}
      </div>

      {mobile && drawer && (
        <BottomSheet label={trees[drawer].label} title={trees[drawer].title} onClose={() => setDrawer(null)} className="h-[70dvh]">
          <div className="flex min-h-0 flex-1 flex-col">{trees[drawer].body}</div>
        </BottomSheet>
      )}

      {previewOpen && card && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-4 md:p-10" onClick={togglePreview}>
          <div className="h-full w-full max-w-4xl rounded-2xl bg-white p-3 dark:bg-stone-900">
            <Preview fileId={card.file.id} ext={card.file.ext} large />
          </div>
        </div>
      )}

      {detailsOpen && card?.duplicate && <DuplicatePanel card={card} onClose={closeDetails} />}

      {pickerOpen && (
        <FolderPicker folders={folders} initialId={lastFolder?.id} onClose={closePicker} onCreate={createFolder} onPick={pickFolder} />
      )}
      <ToastHost />
    </div>
  )
}
