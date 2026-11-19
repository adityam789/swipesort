import { motion, type MotionValue } from 'framer-motion'
import type { Card, Direction, Edge } from '../api/types'
import { isHighlighted } from '../lib/duplicate'
import { BatchCard } from './BatchCard'
import { DuplicateCard } from './DuplicateCard'
import { ManualCard } from './ManualCard'
import { ProposalCard } from './ProposalCard'
import { TaxonomyCard } from './TaxonomyCard'

const POSITION: Record<Direction, string> = {
  up: 'top-0 left-1/2 -translate-x-1/2',
  down: 'bottom-0 left-1/2 -translate-x-1/2',
  left: 'left-0 top-1/2 -translate-y-1/2 max-w-[4.5rem] md:max-w-[8rem]',
  right: 'right-0 top-1/2 -translate-y-1/2 max-w-[4.5rem] md:max-w-[8rem]',
}
const ARROW: Record<Direction, string> = { up: '↑', down: '↓', left: '←', right: '→' }
const ORDER: Direction[] = ['up', 'right', 'down', 'left']

function tone(edge: Edge): string {
  if (edge.action === 'trash' || edge.action === 'trash_all') return 'border-red-300 text-red-700 dark:border-red-900 dark:text-red-300'
  if (edge.action === 'flip' || edge.action === 'inbox' || edge.action === 'reject_all')
    return 'border-amber-300 text-amber-800 dark:border-amber-900 dark:text-amber-300'
  if (edge.action === 'dismiss') return 'border-stone-300 text-stone-600 dark:border-stone-700 dark:text-stone-400'
  if (edge.action === 'details' || edge.action === 'review' || edge.action === 'suggest_folder') return 'border-sky-300 text-sky-800 dark:border-sky-900 dark:text-sky-300'
  if (edge.action === 'defer') return 'border-stone-300 text-stone-600 dark:border-stone-700 dark:text-stone-400'
  return 'border-emerald-300 text-emerald-800 dark:border-emerald-900 dark:text-emerald-300'
}

export interface CardViewProps {
  card: Card
  aimed?: Direction | null
  bind?: () => Record<string, unknown>
  x?: MotionValue<number>
  y?: MotionValue<number>
  rotate?: MotionValue<number>
  onEdgeClick?: (dir: Direction) => void
  /** True when the card just flipped to its alternate layout (plays a quick flip animation). */
  flipped?: boolean
  /** Phone layout: the card fills the screen, edge hints sit on the screen edges. */
  fullscreen?: boolean
}

/** Renders any card: edge labels come from `card.edges` (data-driven); the body is chosen by `card.kind`. */
export function CardView({ card, aimed = null, bind, x, y, rotate, onEdgeClick, flipped = false, fullscreen = false }: CardViewProps) {
  const hotkey = (edge: Edge) =>
    Object.entries(card.keys ?? {}).find(
      ([, k]) => k.action === edge.action && k.label === edge.label && (k.target_folder_id ?? null) === (edge.target_folder_id ?? null),
    )?.[0]
  const edgeButton = (dir: Direction, docked: boolean) => {
    const edge = card.edges[dir]
    if (!edge) return null
    const active = aimed === dir
    const highlighted = isHighlighted(card, edge)
    const place = docked
      ? 'relative min-h-12 min-w-0 flex-1 px-2 text-[12px]'
      : `absolute z-10 px-3 ${POSITION[dir]}`
    return (
      <button
        key={dir}
        type="button"
        data-edge={dir}
        data-aimed={active || undefined}
        data-highlight={highlighted || undefined}
        onClick={() => onEdgeClick?.(dir)}
        className={`${place} rounded-full border bg-white/80 py-1.5 text-center text-xs font-medium leading-tight shadow-sm backdrop-blur transition dark:bg-stone-900/80 ${tone(edge)} ${
          active ? 'scale-105 ring-2 ring-current' : highlighted ? 'font-semibold opacity-100 ring-2 ring-emerald-500' : 'opacity-90 hover:opacity-100'
        }`}
      >
        <span aria-hidden className="mr-1">
          {ARROW[dir]}
        </span>
        {!docked && hotkey(edge) && (
          <kbd className="mr-1 rounded bg-stone-200 px-1 font-mono text-[10px] dark:bg-stone-700">{hotkey(edge)}</kbd>
        )}
        <span className={docked ? 'line-clamp-2 break-words' : ''}>{edge.label}</span>
      </button>
    )
  }
  const cardEl = (
    <motion.div
      key={flipped ? 'flipped' : 'front'}
      {...(bind ? bind() : {})}
      data-testid="card"
      style={{ x, y, rotate, touchAction: 'none' }}
      initial={{ opacity: 0, scale: 0.97, rotateY: flipped ? 90 : 0 }}
      animate={{ opacity: 1, scale: 1, rotateY: 0 }}
      transition={{ duration: flipped ? 0.18 : 0.12 }}
      className={`${fullscreen ? 'min-h-0 flex-1 overflow-y-auto' : 'h-full'} cursor-grab select-none rounded-2xl border border-stone-200 bg-white p-4 shadow-lg active:cursor-grabbing dark:border-stone-800 dark:bg-stone-900`}
    >
      <CardBody card={card} />
    </motion.div>
  )
  if (fullscreen) {
    // Phone: the card gets the full width; edge actions sit in a dock below it, laid out
    // spatially (left · up/down · right) so they still read as fling directions.
    const mid = (['up', 'down'] as Direction[]).filter((d) => card.edges[d])
    return (
      <div data-fullscreen className="flex h-full w-full flex-col gap-2 px-3 pt-1 pb-2" style={{ perspective: 1000 }}>
        {cardEl}
        <nav aria-label="Card actions" className="flex shrink-0 items-stretch gap-2">
          {edgeButton('left', true)}
          {mid.length > 0 && <div className="flex min-w-0 flex-1 flex-col gap-1.5">{mid.map((d) => edgeButton(d, true))}</div>}
          {edgeButton('right', true)}
        </nav>
      </div>
    )
  }
  return (
    <div className="relative mx-auto h-full w-full max-w-xl px-14 py-12 md:px-36" style={{ perspective: 1000 }}>
      {ORDER.map((dir) => edgeButton(dir, false))}
      {cardEl}
    </div>
  )
}

function CardBody({ card }: { card: Card }) {
  switch (card.kind) {
    case 'duplicate':
      return card.duplicate ? <DuplicateCard card={card} /> : <ManualCard card={card} />
    case 'batch':
      return card.batch ? <BatchCard card={card} /> : <ManualCard card={card} />
    case 'folder_proposal':
      return card.proposal ? <ProposalCard card={card} /> : <ManualCard card={card} />
    case 'taxonomy':
      return card.taxonomy ? <TaxonomyCard card={card} /> : <ManualCard card={card} />
    default:
      return <ManualCard card={card} />
  }
}
