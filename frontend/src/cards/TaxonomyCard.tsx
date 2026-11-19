import type { Card, TaxonomyNode } from '../api/types'
import { useSession } from '../store/session'
import { KindChip, Reason } from './Insights'
import { allNodeIds, noDrag, toggleNode } from '../lib/proposal'

function Row({
  node,
  depth,
  checked,
  name,
  onToggle,
  onRename,
}: {
  node: TaxonomyNode
  depth: number
  checked: boolean
  name: string
  onToggle: () => void
  onRename: (v: string) => void
}) {
  return (
    <li data-testid="taxonomy-node" data-node={node.id} className={`flex items-center gap-2 ${depth ? 'pl-6' : ''}`}>
      <input type="checkbox" aria-label={`Create ${name}`} checked={checked} onChange={onToggle} {...noDrag} className="accent-teal-600" />
      <input
        aria-label={`Name of ${node.name}`}
        value={name}
        maxLength={40}
        onChange={(e) => onRename(e.target.value)}
        {...noDrag}
        className={`min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 hover:border-stone-300 focus:border-teal-400 focus:outline-none dark:hover:border-stone-700 ${
          depth ? 'text-sm' : 'font-semibold'
        } ${checked ? '' : 'text-stone-400 line-through'}`}
      />
      <span className="shrink-0 text-xs tabular-nums text-stone-500" title={node.sample.join('\n')}>
        {node.count}
      </span>
    </li>
  )
}

/** Cold start: one "Proposed structure" card; accept all, a subset, or edit names. */
export function TaxonomyCard({ card }: { card: Card }) {
  const tax = card.taxonomy
  const draft = useSession((s) => s.drafts[card.id])
  const setDraft = useSession((s) => s.setDraft)
  if (!tax) return null
  const selected = draft?.accepted_nodes ?? allNodeIds(tax.nodes)
  const renames = draft?.renames ?? {}
  const nameOf = (n: TaxonomyNode) => renames[n.id] ?? n.name
  const toggle = (id: string) => setDraft(card.id, { accepted_nodes: toggleNode(tax.nodes, selected, id) })
  const rename = (id: string, v: string) => setDraft(card.id, { renames: { ...renames, [id]: v } })
  return (
    <div data-testid="taxonomy-card" className="flex h-full flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <KindChip kind={card.kind} />
        <span data-testid="taxonomy-selected" className="text-xs tabular-nums text-stone-500 dark:text-stone-400">
          {selected.length} of {allNodeIds(tax.nodes).length} folders selected
        </span>
      </div>
      <div className="space-y-1 text-center">
        <p className="text-lg font-semibold">Proposed structure</p>
        <Reason reason={card.reason} />
      </div>
      <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto rounded-lg bg-stone-50 p-2 text-left dark:bg-stone-950">
        {tax.nodes.map((n) => (
          <li key={n.id}>
            <ul className="space-y-0.5">
              <Row node={n} depth={0} checked={selected.includes(n.id)} name={nameOf(n)} onToggle={() => toggle(n.id)} onRename={(v) => rename(n.id, v)} />
              {n.children.map((c) => (
                <Row key={c.id} node={c} depth={1} checked={selected.includes(c.id)} name={nameOf(c)} onToggle={() => toggle(c.id)} onRename={(v) => rename(c.id, v)} />
              ))}
            </ul>
          </li>
        ))}
      </ul>
      <div className="flex items-center justify-between text-xs text-stone-500">
        <span>{tax.inbox_count ? `${tax.inbox_count} files fit nowhere yet` : `${tax.total} files`}</span>
        <span className="flex gap-2">
          <button type="button" {...noDrag} onClick={() => setDraft(card.id, { accepted_nodes: allNodeIds(tax.nodes) })} className="underline">
            All
          </button>
          <button type="button" {...noDrag} onClick={() => setDraft(card.id, { accepted_nodes: [] })} className="underline">
            None
          </button>
        </span>
      </div>
    </div>
  )
}
