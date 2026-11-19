import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import { UNAUTHORIZED_EVENT, api } from './api/client'
import type { SessionOut } from './api/types'
import { SessionPicker } from './components/SessionPicker'
import { Pair } from './pages/Pair'
import { Setup } from './pages/Setup'
import { Sort } from './pages/Sort'
import { Watch } from './pages/Watch'
import { useSession } from './store/session'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

function PhoneHome({ onPick, autoJoin }: { onPick: (s: SessionOut) => void; autoJoin: boolean }) {
  return (
    <main className="mx-auto flex min-h-full max-w-md flex-col gap-5 px-5 pt-[max(2.5rem,env(safe-area-inset-top))] pb-[max(2.5rem,env(safe-area-inset-bottom))]">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">SwipeSort</h1>
        <p className="mt-1 text-stone-500 dark:text-stone-400">Pick a session to sort from this phone.</p>
      </div>
      <SessionPicker
        onPick={onPick}
        autoJoin={autoJoin}
        emptyHint="No session is running. Start one on your computer (choose the folders there); it shows up here."
      />
    </main>
  )
}

/** Routes: /pair (phone landing from the QR), desktop setup, phone session picker, sort. */
export function Root() {
  const qc = useQueryClient()
  const [path, setPath] = useState(() => window.location.pathname)
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [autoJoin, setAutoJoin] = useState(true)
  const status = useQuery({ queryKey: ['pair-status'], queryFn: api.pairStatus, retry: 2 })

  const start = useCallback((s: SessionOut) => {
    setSessionId(s.id)
    void useSession.getState().start(s)
  }, [])
  const leave = () => {
    useSession.getState().reset()
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'pair-status' })
    setAutoJoin(false)
    setSessionId(null)
  }
  const go = (to: string) => {
    window.history.pushState(null, '', to)
    setPath(to)
  }
  // Watch mode: open a watched folder's session (its pending queue) in the swipe UI
  const openSession = useCallback(
    async (id: number) => {
      useSession.getState().reset()
      window.history.pushState(null, '', '/')
      setPath('/')
      start(await api.getSession(id))
    },
    [start],
  )

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  // A revoked/expired phone gets 401s: drop the session and show pairing again.
  useEffect(() => {
    const onUnauthorized = () => {
      useSession.getState().reset()
      setSessionId(null)
      void qc.invalidateQueries({ queryKey: ['pair-status'] })
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [qc])

  const paired = () => {
    window.history.replaceState(null, '', '/')
    setPath('/')
    void qc.invalidateQueries({ queryKey: ['pair-status'] })
  }

  if (path === '/pair') {
    const code = new URLSearchParams(window.location.search).get('code')
    return <Pair initialCode={code} onPaired={paired} />
  }
  if (status.isPending) return <div className="flex h-full items-center justify-center text-stone-400">Connecting…</div>
  if (status.isError || !status.data) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center text-stone-500">
        <p>Can't reach SwipeSort. Is the daemon running and is this device on the same network?</p>
        <button type="button" onClick={() => void status.refetch()} className="min-h-11 rounded-lg border border-stone-300 px-4 text-sm dark:border-stone-700">
          Retry
        </button>
      </div>
    )
  }
  if (!status.data.paired) return <Pair onPaired={paired} />
  if (path === '/watch' && status.data.local) {
    const undo = Number(new URLSearchParams(window.location.search).get('undo'))
    return <Watch onBack={() => go('/')} onOpenSession={(id) => void openSession(id)} highlight={Number.isFinite(undo) && undo > 0 ? undo : null} />
  }
  if (sessionId !== null)
    return <Sort sessionId={sessionId} onNew={leave} local={status.data.local} onOpenSession={(id) => void openSession(id)} />
  if (status.data.local) return <Setup onStart={start} onWatch={() => go('/watch')} onOpenSession={(id) => void openSession(id)} />
  return <PhoneHome onPick={start} autoJoin={autoJoin} />
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Root />
    </QueryClientProvider>
  )
}
