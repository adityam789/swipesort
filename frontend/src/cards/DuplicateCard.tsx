import type { Card, DuplicateInfo } from '../api/types'
import { formatDate, formatSize } from '../lib/format'
import { extOf, RELATION_LABEL, similarityPct } from '../lib/duplicate'
import { Preview } from './Preview'

const RELATION_TONE: Record<DuplicateInfo['relation'], string> = {
  exact: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300',
  near_image: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
  near_text: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
  version: 'bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300',
}

export function RelationChip({ dup }: { dup: DuplicateInfo }) {
  return (
    <span
      data-testid="relation-chip"
      data-relation={dup.relation}
      className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${RELATION_TONE[dup.relation]}`}
    >
      {RELATION_LABEL[dup.relation]} · {similarityPct(dup.similarity)}
    </span>
  )
}

export function DuplicateCard({ card }: { card: Card }) {
  const { file, duplicate: dup } = card
  if (!dup) return null
  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <span
          data-testid="kind-chip"
          data-kind="duplicate"
          className="inline-block rounded-full bg-stone-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-stone-600 dark:bg-stone-800 dark:text-stone-400"
        >
          duplicate
        </span>
        <RelationChip dup={dup} />
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-2 gap-2">
        <Side title="This file" name={file.name} previewKey="this" preview={<Preview fileId={file.id} ext={file.ext} />} meta={`${formatSize(file.size)} · ${formatDate(file.mtime)}`} />
        <Side
          title={`Other (${dup.other_side})`}
          name={dup.other_name}
          previewKey="other"
          preview={<Preview fileId={dup.other_preview_url} ext={extOf(dup.other_name)} />}
          meta={`${formatSize(dup.other_size)} · ${formatDate(dup.other_mtime)}`}
        />
      </div>
      <p className="text-center text-xs italic text-stone-500 dark:text-stone-400">{dup.reason || card.reason}</p>
    </div>
  )
}

function Side({ title, name, meta, preview, previewKey }: { title: string; name: string; meta: string; preview: React.ReactNode; previewKey: string }) {
  return (
    <div data-testid={`dup-side-${previewKey}`} className="flex min-h-0 min-w-0 flex-col gap-1">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-stone-400">{title}</p>
      <div className="min-h-0 flex-1">{preview}</div>
      <p className="truncate text-center text-sm font-medium" title={name}>
        {name}
      </p>
      <p className="text-center text-xs text-stone-500 dark:text-stone-400">{meta}</p>
    </div>
  )
}
