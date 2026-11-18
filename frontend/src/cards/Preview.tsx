import { useEffect, useState } from 'react'
import { fetchPreview, type PreviewData } from '../api/client'

type State = { fileId: number; data: PreviewData | null } | null

export function Preview({ fileId, ext, large = false }: { fileId: number; ext: string; large?: boolean }) {
  const [state, setState] = useState<State>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    let url: string | null = null
    fetchPreview(fileId, ctrl.signal)
      .then((data) => {
        if (data?.kind === 'image') url = data.url
        if (!ctrl.signal.aborted) setState({ fileId, data })
        else if (url) URL.revokeObjectURL(url)
      })
      .catch(() => {
        if (!ctrl.signal.aborted) setState({ fileId, data: null })
      })
    return () => {
      ctrl.abort()
      if (url) URL.revokeObjectURL(url)
    }
  }, [fileId])

  const data = state?.fileId === fileId ? state.data : undefined // undefined = loading
  const box = large ? 'h-full w-full' : 'h-full w-full'

  if (data === undefined) {
    return <div className={`${box} animate-pulse rounded-lg bg-stone-200 dark:bg-stone-800`} />
  }
  if (data === null) {
    return (
      <div className={`${box} flex items-center justify-center rounded-lg bg-stone-200 dark:bg-stone-800`}>
        <span className="text-3xl font-semibold uppercase tracking-wider text-stone-500">
          {ext.replace('.', '') || 'file'}
        </span>
      </div>
    )
  }
  if (data.kind === 'image') {
    return <img src={data.url} alt="" draggable={false} className={`${box} rounded-lg object-contain`} />
  }
  return (
    <pre
      className={`${box} overflow-auto whitespace-pre-wrap break-words rounded-lg bg-stone-100 p-3 text-left font-mono text-xs leading-relaxed text-stone-700 dark:bg-stone-900 dark:text-stone-300`}
    >
      {data.text}
    </pre>
  )
}
