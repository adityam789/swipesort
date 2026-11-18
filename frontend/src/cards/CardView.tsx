import { motion, type MotionValue } from 'framer-motion'
import type { Card, Direction, Edge } from '../api/types'
import { ManualCard } from './ManualCard'

const POSITION: Record<Direction, string> = {
  up: 'top-0 left-1/2 -translate-x-1/2',
  down: 'bottom-0 left-1/2 -translate-x-1/2',
  left: 'left-0 top-1/2 -translate-y-1/2 max-w-[5.5rem] md:max-w-[8rem]',
  right: 'right-0 top-1/2 -translate-y-1/2 max-w-[5.5rem] md:max-w-[8rem]',
}
const ARROW: Record<Direction, string> = { up: '↑', down: '↓', left: '←', right: '→' }
const ORDER: Direction[] = ['up', 'right', 'down', 'left']

function tone(edge: Edge): string {
  if (edge.action === 'trash') return 'border-red-300 text-red-700 dark:border-red-900 dark:text-red-300'
  if (edge.action === 'flip' || edge.action === 'inbox')
    return 'border-amber-300 text-amber-800 dark:border-amber-900 dark:text-amber-300'
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
}

/** Renders any card: edge labels come from `card.edges` (data-driven); the body is chosen by `card.kind`. */
export function CardView({ card, aimed = null, bind, x, y, rotate, onEdgeClick, flipped = false }: CardViewProps) {
  const hotkey = (edge: Edge) =>
    Object.entries(card.keys ?? {}).find(
      ([, k]) => k.action === edge.action && k.label === edge.label && (k.target_folder_id ?? null) === (edge.target_folder_id ?? null),
    )?.[0]
  return (
    <div className="relative mx-auto h-full w-full max-w-xl px-14 py-12 md:px-36" style={{ perspective: 1000 }}>
      {ORDER.map((dir) => {
        const edge = card.edges[dir]
        if (!edge) return null
        const active = aimed === dir
        return (
          <button
            key={dir}
            type="button"
            data-edge={dir}
            data-aimed={active || undefined}
            onClick={() => onEdgeClick?.(dir)}
            className={`absolute z-10 rounded-full border bg-white/80 px-3 py-1.5 text-center text-xs font-medium leading-tight shadow-sm backdrop-blur transition dark:bg-stone-900/80 ${tone(edge)} ${POSITION[dir]} ${
              active ? 'scale-110 ring-2 ring-current' : 'opacity-80 hover:opacity-100'
            }`}
          >
            <span aria-hidden className="mr-1">
              {ARROW[dir]}
            </span>
            {hotkey(edge) && (
              <kbd className="mr-1 rounded bg-stone-200 px-1 font-mono text-[10px] dark:bg-stone-700">{hotkey(edge)}</kbd>
            )}
            {edge.label}
          </button>
        )
      })}
      <motion.div
        key={flipped ? 'flipped' : 'front'}
        {...(bind ? bind() : {})}
        data-testid="card"
        style={{ x, y, rotate, touchAction: 'none' }}
        initial={{ opacity: 0, scale: 0.97, rotateY: flipped ? 90 : 0 }}
        animate={{ opacity: 1, scale: 1, rotateY: 0 }}
        transition={{ duration: flipped ? 0.18 : 0.12 }}
        className="h-full cursor-grab select-none rounded-2xl border border-stone-200 bg-white p-4 shadow-lg active:cursor-grabbing dark:border-stone-800 dark:bg-stone-900"
      >
        <CardBody card={card} />
      </motion.div>
    </div>
  )
}

function CardBody({ card }: { card: Card }) {
  switch (card.kind) {
    // M0 only produces manual cards; later kinds get their own bodies (SPEC §13).
    default:
      return <ManualCard card={card} />
  }
}
