import type { Card } from '../api/types'
import { formatDate, formatSize } from '../lib/format'
import { extOf, RELATION_LABEL, similarityPct } from '../lib/duplicate'
import { RelationChip } from './DuplicateCard'
import { Preview } from './Preview'
import { BottomSheet } from '../components/BottomSheet'

/** Side-by-side compare panel for a duplicate card. Purely client-side; Esc or backdrop closes it. */
export function DuplicatePanel({ card, onClose }: { card: Card; onClose: () => void }) {
  const dup = card.duplicate
  if (!dup) return null
  const { file } = card
  const cols = [
    { id: 'this', title: 'This file', name: file.name, path: file.rel_path, size: file.size, mtime: file.mtime, src: file.id as number | string, ext: file.ext },
    { id: 'other', title: `Other copy (${dup.other_side})`, name: dup.other_name, path: dup.other_rel_path, size: dup.other_size, mtime: dup.other_mtime, src: dup.other_preview_url, ext: extOf(dup.other_name) },
  ]
  return (
    <BottomSheet label="Duplicate details" testId="duplicate-panel" onClose={onClose} size="lg" className="h-[88dvh] md:h-full">
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4 pt-0 md:pt-4">
        <div className="flex flex-wrap items-center gap-2">
          <RelationChip dup={dup} />
          <span className="text-sm text-stone-600 dark:text-stone-300">
            {RELATION_LABEL[dup.relation]} match, {similarityPct(dup.similarity)} similar
          </span>
          <button type="button" onClick={onClose} className="ml-auto min-h-11 rounded-lg border border-stone-300 px-3 py-1 md:min-h-0 text-sm hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-800">
            Close (Esc)
          </button>
        </div>
        <p className="text-sm italic text-stone-500 dark:text-stone-400">{dup.reason || card.reason}</p>
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 md:grid-cols-2">
          {cols.map((c) => (
            <section key={c.id} data-testid={`panel-${c.id}`} className="flex min-h-0 flex-col gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-400">{c.title}</h3>
              <div className="h-56 min-h-0 md:h-auto md:flex-1">
                <Preview fileId={c.src} ext={c.ext} large />
              </div>
              <p className="break-words font-medium">{c.name}</p>
              <p className="truncate text-xs text-stone-500" title={c.path}>
                {c.path}
              </p>
              <p className="text-xs text-stone-500">
                {formatSize(c.size)} · {formatDate(c.mtime)}
              </p>
            </section>
          ))}
        </div>
      </div>
    </BottomSheet>
  )
}
