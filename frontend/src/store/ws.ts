import type { StreamEvent } from '../api/types'

export const BACKOFF_BASE_MS = 250
export const BACKOFF_MAX_MS = 5000

/** Reconnect delay for the nth consecutive failed attempt (0-based): 250, 500, 1000, … capped at 5 s. */
export function backoffMs(attempt: number): number {
  return Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** attempt)
}

export function streamUrl(sessionId: number): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}/api/sessions/${sessionId}/stream`
}

export interface StreamHandlers {
  onEvent: (ev: StreamEvent) => void
  /** `reconnect` is true when this is not the first successful connection. */
  onOpen: (reconnect: boolean) => void
  onClose: () => void
}

/**
 * Opens the session event stream and keeps it open (auto-reconnect with capped backoff).
 * Returns a function that closes it for good. Transport only: no store access.
 */
export function openStream(sessionId: number, handlers: StreamHandlers): () => void {
  let stopped = false
  let ws: WebSocket | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let attempt = 0
  let everOpened = false

  const connect = () => {
    timer = null
    if (stopped || typeof WebSocket === 'undefined') return
    const sock = new WebSocket(streamUrl(sessionId))
    ws = sock
    sock.onopen = () => {
      if (stopped || ws !== sock) return
      attempt = 0
      const reconnect = everOpened
      everOpened = true
      handlers.onOpen(reconnect)
    }
    sock.onmessage = (e: MessageEvent) => {
      if (stopped || ws !== sock) return
      try {
        handlers.onEvent(JSON.parse(String(e.data)) as StreamEvent)
      } catch {
        /* ignore malformed frames */
      }
    }
    sock.onclose = () => {
      if (ws !== sock) return
      ws = null
      if (stopped) return
      handlers.onClose()
      timer = setTimeout(connect, backoffMs(attempt++))
    }
    sock.onerror = () => {
      // A close event always follows; reconnect is scheduled there.
    }
  }

  connect()

  return () => {
    stopped = true
    if (timer !== null) clearTimeout(timer)
    timer = null
    const sock = ws
    ws = null
    if (sock) {
      sock.onopen = sock.onmessage = sock.onclose = sock.onerror = null
      sock.close()
    }
  }
}
