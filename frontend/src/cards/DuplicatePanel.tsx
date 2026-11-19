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
    <BottomSheet label="Duplicate details" title="Duplicate details" testId="duplicate-panel" onClose={onClose} size="lg" className="h-[88dvh] md:h-full">
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto overscroll-contain px-4 pt-1 pb-6 md:pt-2">
        <div className="flex flex-wrap items-center gap-2">
          <RelationChip dup={dup} />
          <span className="text-sm text-stone-600 dark:text-stone-300">
            {RELATION_LABEL[dup.relation]} match, {similarityPct(dup.similarity)} similar
          </span>
        </div>
        <p className="text-sm italic leading-relaxed text-stone-500 dark:text-stone-400">{dup.reason || card.reason}</p>
        <div className="grid shrink-0 grid-cols-1 gap-6 md:min-h-0 md:flex-1 md:shrink md:grid-cols-2 md:gap-4">
          {cols.map((c) => (
            <section key={c.id} data-testid={`panel-${c.id}`} className="flex flex-col gap-2 border-t md:min-h-0 border-stone-200 pt-4 first:border-t-0 first:pt-0 md:border-t-0 md:pt-0 dark:border-stone-800">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-400">{c.title}</h3>
              <div className="h-72 shrink-0 overflow-hidden md:min-h-0 md:shrink rounded-lg md:h-auto md:flex-1">
                <Preview fileId={c.src} ext={c.ext} large />
              </div>
              <p className="mt-1 break-words font-medium leading-snug">{c.name}</p>
              <p className="break-all text-xs text-stone-500" title={c.path}>
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
