/** Every gesture threshold in one place (touch, mouse and bottom sheets). */

export const FLING = {
  /** Release past this distance (px) commits toward the aimed edge. */
  distance: 110,
  /** A flick at least this fast (px/ms, along the drag direction) commits... */
  velocity: 0.45,
  /** ...once it has travelled this far (px): short fast flicks count. */
  minDistance: 24,
  /** Distance (px) at which an edge lights up as aimed. */
  aimDistance: 30,
  /** Movement (px) before the gesture locks to the horizontal or vertical axis. */
  axisLockDistance: 10,
  /** Max travel (px) toward a side that has no edge (rubber band). */
  rubberBandLimit: 48,
  /** Spring back when a release does not commit. */
  spring: { type: 'spring', stiffness: 520, damping: 34 } as const,
  /** Haptic tick on commit (ms), where navigator.vibrate exists (Android Chrome). */
  hapticMs: 12,
}

export const SHEET = {
  /** Dragging a bottom sheet down this far (px) dismisses it... */
  dismissDistance: 110,
  /** ...or flicking it down this fast (px/s, framer-motion units). */
  dismissVelocity: 500,
  spring: { type: 'spring', stiffness: 420, damping: 38 } as const,
}

/** Phone layout: full-screen card, drawers become bottom sheets (SPEC §2.1). */
export const MOBILE_QUERY = '(max-width: 767px)'
