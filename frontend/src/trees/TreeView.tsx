import { useEffect, useRef, useState } from 'react'
import { Tree, type NodeRendererProps } from 'react-arborist'
import type { TreeNode } from '../api/types'

interface Row {
  id: string
  name: string
  count: number
  isNew: boolean
  isDir: boolean
  isBundle: boolean
  children?: Row[]
}

function toRows(nodes: TreeNode[]): Row[] {
  return nodes.map((n) => ({
    id: n.path || '/',
    name: n.name,
    count: n.count,
    isNew: n.is_new,
    isDir: n.is_dir,
    isBundle: n.is_bundle,
    // Bundles move as one unit, so they are shown as leaves.
    children: n.is_dir && !n.is_bundle ? toRows(n.children) : undefined,
  }))
}

function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [size, setSize] = useState({ width: 240, height: 320 })
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setSize({ width: Math.max(80, Math.floor(width)), height: Math.max(80, Math.floor(height)) })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, size] as const
}

function NodeRow({ node, style }: NodeRendererProps<Row>) {
  const d = node.data
  return (
    <div
      style={style}
      onClick={() => node.isInternal && node.toggle()}
      className={`flex cursor-default items-center gap-1.5 rounded pr-2 text-sm ${
        d.isNew ? 'bg-emerald-100 font-medium text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200' : ''
      }`}
    >
      <span className="w-3 shrink-0 text-center text-[10px] text-stone-400">
        {node.isInternal ? (node.isOpen ? '▾' : '▸') : ''}
      </span>
      <span aria-hidden className="shrink-0 text-xs">
        {d.isBundle ? '📦' : d.isDir ? '📁' : '·'}
      </span>
      <span className="min-w-0 flex-1 truncate">{d.name}</span>
      {d.isNew && <span className="shrink-0 text-[10px] font-semibold uppercase text-emerald-700 dark:text-emerald-400">new</span>}
      {d.isDir && (
        <span className="shrink-0 rounded-full bg-stone-200 px-1.5 text-[11px] tabular-nums text-stone-600 dark:bg-stone-800 dark:text-stone-400">
          {d.count}
        </span>
      )}
    </div>
  )
}

export function TreeView({ root, emptyLabel }: { root: TreeNode | undefined; emptyLabel: string }) {
  const [ref, { width, height }] = useSize<HTMLDivElement>()
  const rows = root ? toRows(root.children) : []
  return (
    <div ref={ref} className="min-h-0 flex-1">
      {rows.length === 0 ? (
        <p className="p-3 text-sm text-stone-400">{emptyLabel}</p>
      ) : (
        <Tree<Row>
          data={rows}
          width={width}
          height={height}
          rowHeight={28}
          indent={14}
          openByDefault
          disableDrag
          disableDrop
          disableEdit
          disableMultiSelection
        >
          {NodeRow}
        </Tree>
      )}
    </div>
  )
}
