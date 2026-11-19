import type { Card } from '../api/types'
import { noDrag } from '../lib/proposal'
import { useSession } from '../store/session'
import { Thumb } from './BatchCard'
import { ConfidenceMeter, KindChip, Reason } from './Insights'

const THUMBS = 6

/**
 * Folder proposal (SPEC §6.1): "Create Work/PayPal/Onboarding/ for these 5 files?".
 * The name is the only text input in the app; the edit is sent along when the card is accepted.
 */
export function ProposalCard({ card }: { card: Card }) {
  const p = card.proposal
  const draftName = useSession((s) => s.drafts[card.id]?.name)
  const setDraft = useSession((s) => s.setDraft)
  if (!p) return null
  const name = draftName ?? p.name
  const n = p.members.length
  return (
    <div data-testid="proposal-card" className="flex h-full flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <KindChip kind={card.kind} />
        <span className="flex items-center gap-2 text-xs text-stone-500 dark:text-stone-400">
          {p.named_by === 'llm' && <span data-testid="named-by">AI-named</span>}
          <span title="How similar the files are to each other">similarity</span>
          <ConfidenceMeter value={p.cohesion} />
        </span>
      </div>
      <div className="space-y-2 text-center">
        <p className="text-sm text-stone-500 dark:text-stone-400">
          {p.singleton ? 'This file looks like a new topic.' : `${n} files that belong together.`} Create:
        </p>
        <label className="flex items-center justify-center gap-0.5 text-lg font-semibold">
          {p.parent_path && <span className="text-stone-400">{p.parent_path}/</span>}
          <input
            data-testid="proposal-name"
            aria-label="Folder name"
            value={name}
            maxLength={40}
            onChange={(e) => setDraft(card.id, { name: e.target.value })}
            {...noDrag}
            className="w-48 rounded-md border border-teal-300 bg-transparent px-2 py-0.5 text-center text-teal-800 focus:outline-none focus:ring-2 focus:ring-teal-500 dark:border-teal-800 dark:text-teal-200"
          />
          <span className="text-stone-400">/</span>
        </label>
        <Reason reason={card.reason} />
      </div>
      <div data-testid="proposal-thumbs" className="flex flex-wrap justify-center gap-2">
        {p.members.slice(0, THUMBS).map((m) => (
          <Thumb key={m.id} file={m} />
        ))}
      </div>
      <ul
        data-testid="proposal-members"
        className="min-h-0 flex-1 space-y-0.5 overflow-y-auto rounded-lg bg-stone-50 p-2 text-left text-sm dark:bg-stone-950"
      >
        {p.members.map((m) => (
          <li key={m.id} data-testid="proposal-member" className="truncate" title={m.rel_path}>
            {m.name}
          </li>
        ))}
      </ul>
    </div>
  )
}
