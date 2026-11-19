import { motion, useDragControls, type PanInfo } from 'framer-motion'
import { useEffect, type KeyboardEvent, type ReactNode } from 'react'
import { SHEET } from '../gestures/constants'
import { shouldDismissSheet } from '../gestures/sheet'
import { useIsMobile } from '../gestures/useIsMobile'

export interface BottomSheetProps {
  /** Accessible name of the dialog. */
  label: string
  onClose: () => void
  children: ReactNode
  /** Visible heading (optional; the drag handle is always shown on phones). */
  title?: ReactNode
  /** Desktop: render as a centred modal (default) or let the caller lay it out (`'sheet'` = always a sheet). */
  desktop?: 'modal' | 'sheet'
  /** Close on Escape (default true). Turn off when the content handles Escape itself. */
  closeOnEscape?: boolean
  /** Desktop modal size: 'md' (dialogs) or 'lg' (side-by-side compare). */
  size?: 'md' | 'lg'
  /** Extra classes for the panel (e.g. a fixed height). */
  className?: string
  onKeyDown?: (e: KeyboardEvent) => void
  testId?: string
}

/**
 * Phone: a bottom sheet (drag the handle down or flick to dismiss; safe-area aware).
 * Desktop: the same content as a centred modal. Backdrop tap closes either way.
 */
export function BottomSheet({
  label,
  onClose,
  children,
  title,
  desktop = 'modal',
  closeOnEscape = true,
  size = 'md',
  className = '',
  onKeyDown,
  testId,
}: BottomSheetProps) {
  const mobile = useIsMobile()
  const sheet = mobile || desktop === 'sheet'
  const controls = useDragControls()

  useEffect(() => {
    if (!closeOnEscape) return
    const onKey = (e: globalThis.KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [closeOnEscape, onClose])

  const onDragEnd = (_: unknown, info: PanInfo) => {
    if (shouldDismissSheet(info.offset.y, info.velocity.y)) onClose()
  }

  return (
    <div
      data-testid={testId ? `${testId}-backdrop` : 'sheet-backdrop'}
      className={`fixed inset-0 z-50 flex justify-center bg-black/40 ${sheet ? 'items-end' : 'items-center p-6'}`}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      style={{ overscrollBehavior: 'contain' }}
    >
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-label={label}
        data-testid={testId}
        data-sheet={sheet || undefined}
        onKeyDown={onKeyDown}
        drag={sheet ? 'y' : false}
        dragControls={controls}
        dragListener={false}
        dragConstraints={{ top: 0, bottom: 0 }}
        dragElastic={{ top: 0, bottom: 0.9 }}
        onDragEnd={onDragEnd}
        initial={sheet ? { y: '100%' } : { opacity: 0, scale: 0.98 }}
        animate={sheet ? { y: 0 } : { opacity: 1, scale: 1 }}
        transition={SHEET.spring}
        className={`flex w-full flex-col overflow-hidden bg-white shadow-xl dark:bg-stone-900 ${
          sheet ? 'max-h-[88dvh] rounded-t-2xl pb-[env(safe-area-inset-bottom)]' : `rounded-2xl ${size === 'lg' ? 'h-full max-w-5xl' : 'max-h-[85vh] max-w-lg'}`
        } ${className}`}
      >
        {sheet && (
          <div
            data-testid="sheet-handle"
            onPointerDown={(e) => controls.start(e)}
            className="flex min-h-7 shrink-0 cursor-grab touch-none items-center justify-center px-4 pt-2"
          >
            <span aria-hidden className="h-1.5 w-10 rounded-full bg-stone-300 dark:bg-stone-700" />
          </div>
        )}
        {title && (
          <div className={`flex shrink-0 items-center gap-3 px-4 ${sheet ? 'pb-2' : 'pt-3 pb-1'}`}>
            <div className="min-w-0 flex-1 truncate text-base font-semibold leading-none">{title}</div>
            <button
              type="button"
              aria-label="Close"
              data-testid="sheet-close"
              onClick={onClose}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-xl leading-none text-stone-500 hover:bg-stone-100 md:h-8 md:w-8 dark:hover:bg-stone-800"
            >
              ×
            </button>
          </div>
        )}
        {children}
      </motion.div>
    </div>
  )
}
