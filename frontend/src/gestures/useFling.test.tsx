import { render, renderHook } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Edges } from './useFling'
import { aimedDirection, resolveFling, useFling } from './useFling'

const edges: Edges = {
  right: { label: 'Move', action: 'pick', target_folder_id: 1 },
  left: { label: 'Choose folder…', action: 'picker' },
  up: { label: 'Later', action: 'defer' },
  down: { label: 'Trash', action: 'trash' },
}

describe('useFling keys', () => {
  it('maps arrow keys to directions', async () => {
    const onFling = vi.fn()
    renderHook(() => useFling({ edges, onFling }))
    const user = userEvent.setup()
    await user.keyboard('{ArrowRight}{ArrowLeft}{ArrowUp}{ArrowDown}')
    expect(onFling.mock.calls.map((c) => c[0])).toEqual(['right', 'left', 'up', 'down'])
  })

  it('ignores directions without an edge', async () => {
    const onFling = vi.fn()
    renderHook(() => useFling({ edges: { down: edges.down }, onFling }))
    await userEvent.setup().keyboard('{ArrowRight}{ArrowDown}')
    expect(onFling.mock.calls.map((c) => c[0])).toEqual(['down'])
  })

  it('ignores keys when focus is in an input', async () => {
    const onFling = vi.fn()
    const onUndo = vi.fn()
    const { getByLabelText } = render(
      (() => {
        function C() {
          useFling({ edges, onFling, onUndo })
          return <input aria-label="name" />
        }
        return <C />
      })(),
    )
    const user = userEvent.setup()
    await user.click(getByLabelText('name'))
    await user.keyboard('{ArrowRight}z')
    expect(onFling).not.toHaveBeenCalled()
    expect(onUndo).not.toHaveBeenCalled()
  })

  it('handles undo, defer, preview and picker keys', async () => {
    const h = { onFling: vi.fn(), onUndo: vi.fn(), onDefer: vi.fn(), onPreview: vi.fn(), onPicker: vi.fn() }
    renderHook(() => useFling({ edges, ...h }))
    const user = userEvent.setup()
    await user.keyboard('z')
    await user.keyboard('{Control>}z{/Control}')
    await user.keyboard('{Meta>}z{/Meta}')
    await user.keyboard('{Tab}')
    await user.keyboard(' ')
    await user.keyboard('n')
    expect(h.onUndo).toHaveBeenCalledTimes(3)
    expect(h.onDefer).toHaveBeenCalledTimes(1)
    expect(h.onPreview).toHaveBeenCalledTimes(1)
    expect(h.onPicker).toHaveBeenCalledTimes(1)
    expect(h.onFling).not.toHaveBeenCalled()
  })

  it('does nothing when disabled', async () => {
    const onFling = vi.fn()
    renderHook(() => useFling({ edges, onFling, enabled: false }))
    await userEvent.setup().keyboard('{ArrowRight}')
    expect(onFling).not.toHaveBeenCalled()
  })
})

describe('drag resolution', () => {
  it('commits by distance or by velocity, only toward existing edges', () => {
    expect(resolveFling([150, 10], [0.1, 0], edges)).toBe('right')
    expect(resolveFling([50, 0], [0.1, 0], edges)).toBeNull()
    expect(resolveFling([60, 0], [1, 0], edges)).toBe('right')
    expect(resolveFling([-5, -200], [0, 0], edges)).toBe('up')
    expect(resolveFling([150, 0], [0, 0], { down: edges.down })).toBeNull()
  })

  it('aims once past the aim distance', () => {
    expect(aimedDirection([10, 0], edges)).toBeNull()
    expect(aimedDirection([-40, 5], edges)).toBe('left')
  })
})
