import { useState } from 'react'
import type { Card, FileInfo } from '../api/types'
import { KindChip, Reason } from './Insights'

const THUMBS = 6
const pct = (p: number) => `${Math.round(p * 100)}%`

function Thumb({ file }: { file: FileInfo }) {
  const [failed, setFailed] = useState(false)
  return (
    <div
      data-testid="batch-thumb"
      className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-stone-200 dark:bg-stone-800"
    >
      {failed ? (
        <span className="text-[10px] font-semibold uppercase text-stone-500">{file.ext.replace('.', '') || 'file'}</span>
      ) : (
        <img
          src={`/api/files/${file.id}/preview`}
          alt=""
          loading="lazy"
          draggable={false}
          onError={() => setFailed(true)}
          className="h-full w-full object-cover"
        />
      )}
    </div>
  )
}

/** Body of a batch card: N files that share one target, confirmed (or not) as a unit. */
export function BatchCard({ card }: { card: Card }) {
  const batch = card.batch
  if (!batch) return null
  const n = batch.members.length
  const noun = n === 1 ? 'file' : 'files'
  const target = batch.target_path || '(root)'
  return (
    <div data-testid="batch-card" data-batch-kind={batch.kind} className="flex h-full flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <KindChip kind={card.kind} />
        <span data-testid="batch-min-confidence" className="text-xs tabular-nums text-stone-500 dark:text-stone-400">
          min confidence {pct(batch.min_confidence)}
        </span>
      </div>
      <div className="space-y-1 text-center">
        {batch.kind === 'inbox' ? (
          <p data-testid="batch-summary" className="text-lg font-semibold leading-snug">
            Couldn&apos;t place these <span data-testid="batch-count">{n}</span> {noun} — send them to{' '}
            <span data-testid="batch-target" className="break-all text-amber-700 dark:text-amber-300">
              {target}/
            </span>
            ?
          </p>
        ) : (
          <>
            <p className="leading-none">
              <span data-testid="batch-count" className="text-5xl font-bold tabular-nums">
                {n}
              </span>{' '}
              <span className="text-lg text-stone-500 dark:text-stone-400">{noun}</span>
            </p>
            <p data-testid="batch-summary" className="text-lg font-semibold leading-snug">
              <span aria-hidden>→ </span>
              <span data-testid="batch-target" className="break-all text-emerald-700 dark:text-emerald-300">
                {target}
              </span>
            </p>
          </>
        )}
        <Reason reason={card.reason} />
      </div>
      <div data-testid="batch-thumbs" className="flex flex-wrap justify-center gap-2">
        {batch.members.slice(0, THUMBS).map((m) => (
          <Thumb key={m.id} file={m} />
        ))}
      </div>
      <ul
        data-testid="batch-members"
        className="min-h-0 flex-1 space-y-0.5 overflow-y-auto rounded-lg bg-stone-50 p-2 text-left text-sm dark:bg-stone-950"
      >
        {batch.members.map((m) => (
          <li key={m.id} data-testid="batch-member" className="truncate" title={m.rel_path}>
            {m.name}
          </li>
        ))}
      </ul>
    </div>
  )
}
