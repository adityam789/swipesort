import { useDrag } from '@use-gesture/react'
import { animate, useMotionValue, useTransform } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import type { Direction, Edge } from '../api/types'
import { FLING } from './constants'

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
  /** A tap (no drag) on the card, e.g. open the large preview on touch screens. */
  onTap?: () => void
  /** Overrides for FLING (gestures/constants.ts): commit distance in px. */
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
  aimDistance: number = FLING.aimDistance,
): Direction | null {
  const [mx, my] = movement
  if (Math.hypot(mx, my) < aimDistance) return null
  const dir = directionOf(mx, my)
  return edges[dir] ? dir : null
}

const UNIT: Record<Direction, [number, number]> = { right: [1, 0], left: [-1, 0], down: [0, 1], up: [0, -1] }

/**
 * Pure: should this release commit a fling? `velocity` is signed (px/ms).
 * Commits past `distance`, or on a fast flick along the drag direction once past `minDistance`;
 * a fast flick back toward the centre cancels.
 */
export function resolveFling(
  movement: [number, number],
  velocity: [number, number],
  edges: Edges,
  opts: Pick<FlingOptions, 'distance' | 'velocity' | 'minDistance'> = {},
): Direction | null {
  const { distance = FLING.distance, velocity: vThreshold = FLING.velocity, minDistance = FLING.minDistance } = opts
  const dist = Math.hypot(movement[0], movement[1])
  if (dist === 0) return null
  const dir = directionOf(movement[0], movement[1])
  if (!edges[dir]) return null
  const [ux, uy] = UNIT[dir]
  const along = velocity[0] * ux + velocity[1] * uy
  if (along <= -vThreshold) return null
  if (dist >= distance) return dir
  return dist >= minDistance && along >= vThreshold ? dir : null
}

export type Axis = 'x' | 'y'

/** Pure: lock to the dominant axis once the drag passes `lockDistance` (up vs left/right). */
export function lockAxis(
  movement: [number, number],
  axis: Axis | null,
  lockDistance: number = FLING.axisLockDistance,
): { axis: Axis | null; movement: [number, number] } {
  const [mx, my] = movement
  let a = axis
  if (a === null && Math.hypot(mx, my) >= lockDistance) a = Math.abs(mx) >= Math.abs(my) ? 'x' : 'y'
  if (a === 'x') return { axis: a, movement: [mx, 0] }
  if (a === 'y') return { axis: a, movement: [0, my] }
  return { axis: a, movement: [mx, my] }
}

/** Pure: resistance toward a side without an edge; approaches `limit` asymptotically. */
export function rubberBand(value: number, limit: number = FLING.rubberBandLimit): number {
  const d = Math.abs(value)
  return Math.sign(value) * limit * (1 - 1 / (d / limit + 1))
}

/** Pure: where the card is drawn for a drag offset (free toward edges, rubber band elsewhere). */
export function displayOffset(movement: [number, number], edges: Edges): [number, number] {
  const [mx, my] = movement
  const bx = edges[mx >= 0 ? 'right' : 'left'] ? mx : rubberBand(mx)
  const by = edges[my >= 0 ? 'down' : 'up'] ? my : rubberBand(my)
  return [bx, by]
}

export function haptic(ms: number = FLING.hapticMs): void {
  try {
    if (typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function') navigator.vibrate(ms)
  } catch {
    /* vibration blocked (no user activation, iframe policy): ignore */
  }
}

/** The subset of use-gesture's drag state the handler reads (velocity is unsigned there). */
export interface DragState {
  first: boolean
  active: boolean
  last: boolean
  movement: [number, number]
  velocity: [number, number]
  direction: [number, number]
  /** use-gesture `filterTaps`: the pointer went down and up without dragging. */
  tap?: boolean
}

interface Settable {
  set: (v: number) => void
}

export interface DragDeps<M extends Settable> {
  x: M
  y: M
  options: () => FlingOptions
  setAimed: (d: Direction | null) => void
  springBack: (mv: M) => void
  haptic: () => void
}

/** Drag physics as a plain function of gesture state (unit-testable without pointer events). */
export function createDragHandler<M extends Settable>(deps: DragDeps<M>): (s: DragState) => void {
  let axis: Axis | null = null
  return (s) => {
    const o = deps.options()
    if (o.enabled === false) return
    if (s.first) axis = null
    const locked = lockAxis(s.movement, axis)
    axis = locked.axis
    const m = locked.movement
    if (s.active) {
      const [dx, dy] = displayOffset(m, o.edges)
      deps.x.set(dx)
      deps.y.set(dy)
      deps.setAimed(aimedDirection(m, o.edges, o.aimDistance))
      return
    }
    if (!s.last) return
    axis = null
    deps.setAimed(null)
    if (s.tap) {
      deps.springBack(deps.x)
      deps.springBack(deps.y)
      o.onTap?.()
      return
    }
    const v: [number, number] = [
      m[0] === 0 ? 0 : s.velocity[0] * (s.direction[0] || Math.sign(m[0])),
      m[1] === 0 ? 0 : s.velocity[1] * (s.direction[1] || Math.sign(m[1])),
    ]
    const dir = resolveFling(m, v, o.edges, o)
    if (dir) {
      deps.x.set(0)
      deps.y.set(0)
      deps.haptic()
      o.onFling(dir)
    } else {
      deps.springBack(deps.x)
      deps.springBack(deps.y)
    }
  }
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

  const handler = useRef<((s: DragState) => void) | null>(null)
  const bind = useDrag(
    (state) => {
      handler.current ??= createDragHandler({
        x,
        y,
        options: () => optsRef.current,
        setAimed,
        springBack: (mv) => void animate(mv, 0, FLING.spring),
        haptic: () => haptic(),
      })
      handler.current(state)
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
