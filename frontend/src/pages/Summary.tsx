import { useSession } from '../store/session'

export function Summary({ onNew }: { onNew: () => void }) {
  const counts = useSession((s) => s.counts)
  const undoAll = useSession((s) => s.undoAll)
  const stats: [string, number][] = [
    ['Moved', counts.moved],
    ['Inbox', counts.inbox],
    ['Trashed', counts.trashed],
    ['Deferred', counts.deferred],
  ]
  return (
    <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center gap-6 px-5 text-center">
      <h2 className="text-2xl font-semibold">All sorted</h2>
      <dl className="grid w-full grid-cols-2 sm:grid-cols-4 gap-3">
        {stats.map(([k, v]) => (
          <div key={k} className="rounded-xl border border-stone-200 bg-white p-4 dark:border-stone-800 dark:bg-stone-900">
            <dd className="text-3xl font-semibold tabular-nums">{v}</dd>
            <dt className="text-sm text-stone-500">{k}</dt>
          </div>
        ))}
      </dl>
      <p className="text-sm text-stone-500">Trashed files are kept in .swipesort/trash until you purge them.</p>
      <div className="flex gap-3">
        <button
          type="button"
          onClick={() => void undoAll()}
          className="rounded-xl border border-stone-300 px-4 py-2 text-sm font-medium hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-800"
        >
          Undo all
        </button>
        <button type="button" onClick={onNew} className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700">
          New session
        </button>
      </div>
    </div>
  )
}
