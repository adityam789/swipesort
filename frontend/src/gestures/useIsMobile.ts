import { useSyncExternalStore } from 'react'
import { MOBILE_QUERY } from './constants'

function query(): MediaQueryList | null {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(MOBILE_QUERY) : null
}

function subscribe(cb: () => void): () => void {
  const mq = query()
  if (!mq) return () => undefined
  mq.addEventListener('change', cb)
  return () => mq.removeEventListener('change', cb)
}

/** True below the md breakpoint (phones). */
export function useIsMobile(): boolean {
  return useSyncExternalStore(subscribe, () => query()?.matches ?? false, () => false)
}
