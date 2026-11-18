import type { Card, DuplicateInfo, Edge } from '../api/types'

export const RELATION_LABEL: Record<DuplicateInfo['relation'], string> = {
  exact: 'Exact',
  near_image: 'Near',
  near_text: 'Near',
  version: 'Version',
}

export const similarityPct = (s: number) => `${Math.round(s * 100)}%`

export function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i > 0 ? name.slice(i) : ''
}

/** Edge action the server recommends for a duplicate card. */
const RECOMMENDED_ACTION = { keep_this: 'keep_this', trash_this: 'trash', keep_both: 'keep_both' } as const

/** Whether an edge should be emphasised: explicit `edge.highlight`, or the duplicate's recommended action. */
export function isHighlighted(card: Card, edge: Edge): boolean {
  if (edge.highlight) return true
  return !!card.duplicate && edge.action === RECOMMENDED_ACTION[card.duplicate.recommend]
}
