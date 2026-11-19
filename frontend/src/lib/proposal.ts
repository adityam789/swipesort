import type { TaxonomyNode } from '../api/types'

/** Stops a drag from starting when the user interacts with a form control inside a card. */
export const noDrag = { onPointerDown: (e: { stopPropagation: () => void }) => e.stopPropagation() }

/** Every node id of the tree (the default selection: accept all). */
export function allNodeIds(nodes: TaxonomyNode[]): string[] {
  return nodes.flatMap((n) => [n.id, ...allNodeIds(n.children)])
}

/** Toggle one node: a top folder takes its children along; a child brings its parent in. */
export function toggleNode(nodes: TaxonomyNode[], selected: string[], id: string): string[] {
  const sel = new Set(selected)
  const top = nodes.find((n) => n.id === id)
  const parent = nodes.find((n) => n.children.some((c) => c.id === id))
  if (sel.has(id)) {
    sel.delete(id)
    top?.children.forEach((c) => sel.delete(c.id))
  } else {
    sel.add(id)
    top?.children.forEach((c) => sel.add(c.id))
    if (parent) sel.add(parent.id)
  }
  return allNodeIds(nodes).filter((x) => sel.has(x))
}
