import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import { deviceName } from '../lib/device'

function message(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 429) return 'Too many attempts. Wait a few minutes, then scan a fresh code.'
    if (e.status === 403) return 'That code is invalid or expired. Show a new QR code on your computer (Pair phone) and scan it again.'
    return e.message
  }
  return 'Could not reach SwipeSort. Is your phone on the same Wi-Fi as the computer?'
}

/**
 * Phone landing page from the QR (`/pair?code=...`): redeems the code for a device token (an
 * HttpOnly cookie), then hands over to the session picker. Also lets you type the code.
 */
export function Pair({ onPaired, initialCode }: { onPaired: () => void; initialCode?: string | null }) {
  const [code, setCode] = useState(initialCode ?? '')
  const [state, setState] = useState<'idle' | 'busy' | 'error'>(initialCode ? 'busy' : 'idle')
  const [error, setError] = useState<string | null>(null)
  const tried = useRef(false)

  const submit = async (value: string) => {
    setState('busy')
    setError(null)
    try {
      await api.pair(value.trim(), deviceName(navigator.userAgent))
      onPaired()
    } catch (e) {
      setError(message(e))
      setState('error')
    }
  }

  useEffect(() => {
    // Auto-redeem the code from the QR once (StrictMode mounts effects twice; codes are single-use).
    if (!initialCode || tried.current) return
    tried.current = true
    api.pair(initialCode.trim(), deviceName(navigator.userAgent)).then(onPaired, (e: unknown) => {
      setError(message(e))
      setState('error')
    })
  }, [initialCode, onPaired])

  return (
    <main className="mx-auto flex min-h-full max-w-md flex-col justify-center gap-5 px-5 pt-[max(2.5rem,env(safe-area-inset-top))] pb-[max(2.5rem,env(safe-area-inset-bottom))]">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">SwipeSort</h1>
        <p className="mt-1 text-stone-500 dark:text-stone-400">Pair this phone with your computer.</p>
      </div>
      {state === 'busy' ? (
        <p data-testid="pairing" className="text-sm text-stone-500">
          Pairing…
        </p>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (code.trim()) void submit(code)
          }}
        >
          <p className="text-sm text-stone-600 dark:text-stone-300">
            On your computer, open SwipeSort and choose <b>Pair phone</b>. Scan the QR code, or type the code shown there.
          </p>
          <label className="block text-sm font-medium">
            Pairing code
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              autoCapitalize="characters"
              autoComplete="one-time-code"
              spellCheck={false}
              className="mt-1.5 block min-h-11 w-full rounded-lg border border-stone-300 bg-white px-3 font-mono text-lg tracking-widest outline-none focus:border-emerald-500 dark:border-stone-700 dark:bg-stone-900"
            />
          </label>
          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={!code.trim()}
            className="min-h-11 w-full rounded-xl bg-emerald-600 px-6 font-medium text-white disabled:opacity-40"
          >
            Pair
          </button>
        </form>
      )}
    </main>
  )
}
