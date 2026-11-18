import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import type { SessionOut } from './api/types'
import { Setup } from './pages/Setup'
import { Sort } from './pages/Sort'
import { useSession } from './store/session'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

export default function App() {
  const [sessionId, setSessionId] = useState<number | null>(null)

  const start = (s: SessionOut) => {
    setSessionId(s.id)
    void useSession.getState().start(s)
  }
  const newSession = () => {
    useSession.getState().reset()
    queryClient.clear()
    setSessionId(null)
  }

  return (
    <QueryClientProvider client={queryClient}>
      {sessionId === null ? <Setup onStart={start} /> : <Sort sessionId={sessionId} onNew={newSession} />}
    </QueryClientProvider>
  )
}
