import { useEffect } from 'react'
import { useSession } from '../store/session'

export function ToastHost() {
  const toast = useSession((s) => s.toast)
  const dismiss = useSession((s) => s.dismissToast)
  const undo = useSession((s) => s.undo)

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(dismiss, toast.kind === 'error' ? 6000 : 4000)
    return () => clearTimeout(t)
  }, [toast, dismiss])

  if (!toast) return null
  return (
    <div
      role="status"
      className={`fixed bottom-[calc(3.75rem+env(safe-area-inset-bottom))] left-1/2 md:bottom-4 z-40 flex max-w-[92vw] -translate-x-1/2 items-center gap-3 rounded-xl px-4 py-2.5 text-sm shadow-lg ${
        toast.kind === 'error'
          ? 'bg-red-600 text-white'
          : 'bg-stone-900 text-stone-50 dark:bg-stone-100 dark:text-stone-900'
      }`}
    >
      <span className="truncate">{toast.message}</span>
      {toast.undoable && (
        <button
          type="button"
          onClick={() => {
            dismiss()
            void undo()
          }}
          className="min-h-11 shrink-0 font-semibold underline underline-offset-2 md:min-h-0"
        >
          Undo
        </button>
      )}
    </div>
  )
}
