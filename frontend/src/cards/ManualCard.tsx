import type { Card } from '../api/types'
import { formatDate, formatSize } from '../lib/format'
import { Preview } from './Preview'

export function ManualCard({ card }: { card: Card }) {
  const { file } = card
  return (
    <div className="flex h-full flex-col gap-3">
      <div className="min-h-0 flex-1">
        <Preview fileId={file.id} ext={file.ext} />
      </div>
      <div className="space-y-1 text-center">
        <h2 className="break-words text-lg font-semibold leading-tight" title={file.name}>
          {file.name}
        </h2>
        <p className="text-sm text-stone-500 dark:text-stone-400">
          {formatSize(file.size)} · {formatDate(file.mtime)}
          {file.is_bundle ? ' · bundle' : ''}
        </p>
        <p className="truncate text-xs text-stone-400 dark:text-stone-500" title={file.rel_path}>
          {file.rel_path}
        </p>
        {card.reason && <p className="text-xs italic text-stone-500 dark:text-stone-400">{card.reason}</p>}
      </div>
    </div>
  )
}
