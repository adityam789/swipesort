import { useDrag } from '@use-gesture/react'
import { animate, useMotionValue, useTransform } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import type { Direction, Edge } from '../api/types'

export type Edges = Partial<Record<Direction, Edge>>

export interface FlingOptions {
  edges: Edges
  onFling: (dir: Direction) => void
  /** Extra hotkeys declared by the card (`card.keys`), e.g. {"1": Edge}. Data-driven. */
  keys?: Record<string, Edge>
  onKey?: (edge: Edge, key: string) => void
  enabled?: boolean
  onUndo?: () => void
  onDefer?: () => void
  onPreview?: () => void
  onPicker?: () => void
  /** Distance in px that commits a fling. */
  distance?: number
  /** Velocity (px/ms) that commits a fling once past `minDistance`. */
  velocity?: number
  minDistance?: number
  /** Distance in px at which an edge starts being "aimed". */
  aimDistance?: number
}

const KEY_DIRECTIONS: Record<string, Direction> = {
  ArrowRight: 'right',
  ArrowLeft: 'left',
  ArrowUp: 'up',
  ArrowDown: 'down',
}

export function directionOf(mx: number, my: number): Direction {
  if (Math.abs(mx) >= Math.abs(my)) return mx >= 0 ? 'right' : 'left'
  return my >= 0 ? 'down' : 'up'
}

/** Pure: which edge (if any) does this drag aim at? */
export function aimedDirection(
  movement: [number, number],
  edges: Edges,
  aimDistance = 30,
): Direction | null {
  const [mx, my] = movement
  if (Math.hypot(mx, my) < aimDistance) return null
  const dir = directionOf(mx, my)
  return edges[dir] ? dir : null
}

/** Pure: should this release commit a fling? */
export function resolveFling(
  movement: [number, number],
  velocity: [number, number],
  edges: Edges,
  opts: Pick<FlingOptions, 'distance' | 'velocity' | 'minDistance'> = {},
): Direction | null {
  const { distance = 110, velocity: vThreshold = 0.5, minDistance = 40 } = opts
  const dist = Math.hypot(movement[0], movement[1])
  const speed = Math.hypot(velocity[0], velocity[1])
  if (dist < distance && !(speed >= vThreshold && dist >= minDistance)) return null
  const dir = directionOf(movement[0], movement[1])
  return edges[dir] ? dir : null
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable
}

/**
 * One hook for touch swipe, mouse drag and keyboard. Returns gesture bindings
 * plus motion values for a framer-motion element, and the currently aimed edge.
 */
export function useFling(options: FlingOptions) {
  const { enabled = true } = options
  const optsRef = useRef(options)
  useEffect(() => {
    optsRef.current = options
  })

  const x = useMotionValue(0)
  const y = useMotionValue(0)
  const rotate = useTransform(x, [-300, 0, 300], [-8, 0, 8])
  const [aimed, setAimed] = useState<Direction | null>(null)

  const bind = useDrag(
    ({ active, movement, velocity, last }) => {
      const o = optsRef.current
      if (o.enabled === false) return
      if (active) {
        x.set(movement[0])
        y.set(movement[1])
        setAimed(aimedDirection(movement, o.edges, o.aimDistance))
        return
      }
      if (last) {
        setAimed(null)
        const dir = resolveFling(movement, velocity, o.edges, o)
        if (dir) {
          x.set(0)
          y.set(0)
          o.onFling(dir)
        } else {
          animate(x, 0, { type: 'spring', stiffness: 500, damping: 35 })
          animate(y, 0, { type: 'spring', stiffness: 500, damping: 35 })
        }
      }
    },
    { filterTaps: true, pointer: { touch: true }, enabled },
  )

  useEffect(() => {
    if (!enabled) return
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || isEditableTarget(e.target)) return
      const o = optsRef.current
      const key = e.key
      const mod = e.metaKey || e.ctrlKey

      if ((key === 'z' || key === 'Z') && !e.altKey && (mod || !e.shiftKey)) {
        e.preventDefault()
        o.onUndo?.()
        return
      }
      if (mod || e.altKey) return
      const keyEdge = o.keys?.[key]
      if (keyEdge) {
        e.preventDefault()
        o.onKey?.(keyEdge, key)
        return
      }
      if (key === 'Tab') {
        e.preventDefault()
        o.onDefer?.()
        return
      }
      if (key === ' ' || key === 'Spacebar') {
        e.preventDefault()
        o.onPreview?.()
        return
      }
      if (key === 'n' || key === 'N') {
        e.preventDefault()
        o.onPicker?.()
        return
      }
      const dir = KEY_DIRECTIONS[key]
      if (dir) {
        e.preventDefault()
        if (o.edges[dir]) o.onFling(dir)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled])

  return { bind, x, y, rotate, aimed }
}
