import type { Candidate, CardKind } from '../api/types'

const CHIP: Record<string, string> = {
  confirm: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300',
  choices: 'bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300',
  inbox: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
  batch: 'bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300',
  manual: 'bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-400',
}

export function KindChip({ kind }: { kind: CardKind }) {
  return (
    <span
      data-testid="kind-chip"
      data-kind={kind}
      className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${CHIP[kind] ?? CHIP.manual}`}
    >
      {kind}
    </span>
  )
}

const pct = (p: number) => `${Math.round(p * 100)}%`

export function ConfidenceMeter({ value }: { value: number }) {
  const w = Math.max(0, Math.min(1, value)) * 100
  return (
    <span data-testid="confidence" className="inline-flex items-center gap-1.5 text-xs tabular-nums text-stone-500 dark:text-stone-400">
      <span className="h-1.5 w-14 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-700">
        <span className="block h-full rounded-full bg-emerald-500" style={{ width: `${w}%` }} />
      </span>
      {pct(value)}
    </span>
  )
}

export function CandidateList({ candidates }: { candidates: Candidate[] }) {
  if (!candidates.length) return null
  return (
    <ul data-testid="candidates" className="mx-auto max-w-full space-y-0.5 text-xs text-stone-400 dark:text-stone-500">
      {candidates.slice(0, 3).map((c) => (
        <li key={c.folder_id} className="flex justify-between gap-3">
          <span className="truncate" title={c.path}>
            {c.path || '/'}
          </span>
          <span className="tabular-nums">{pct(c.p)}</span>
        </li>
      ))}
    </ul>
  )
}

/** The "why": a short sentence (LLM-written when available), shown prominently. */
export function Reason({ reason }: { reason: string }) {
  if (!reason) return null
  return (
    <p data-testid="reason" className="text-sm font-medium leading-snug text-stone-700 dark:text-stone-200">
      {reason}
    </p>
  )
}

/** Where Right/accept will put the file, e.g. "Work/PayPal/_Inbox". */
export function Destination({ destination }: { destination?: string | null }) {
  if (!destination) return null
  return (
    <p data-testid="destination" className="break-all text-xs font-medium text-emerald-700 dark:text-emerald-300" title={destination}>
      <span aria-hidden>→ </span>
      {destination}
    </p>
  )
}
