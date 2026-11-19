import { render, renderHook } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Edges } from './useFling'
import { FLING } from './constants'
import type { DragState } from './useFling'
import { aimedDirection, createDragHandler, displayOffset, lockAxis, resolveFling, rubberBand, useFling } from './useFling'

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

  it('fires card-declared keys (data-driven) and ignores undeclared ones', async () => {
    const keys = {
      '1': { label: 'A', action: 'pick', target_folder_id: 1 },
      '2': { label: 'B', action: 'pick', target_folder_id: 2 },
    } as const
    const onKey = vi.fn()
    renderHook(() => useFling({ edges, onFling: vi.fn(), keys, onKey }))
    await userEvent.setup().keyboard('213')
    expect(onKey.mock.calls.map((c) => [c[1], c[0].target_folder_id])).toEqual([
      ['2', 2],
      ['1', 1],
    ])
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

describe('touch physics', () => {
  it('a short fast flick commits, the same distance slowly does not', () => {
    expect(resolveFling([30, 2], [0.9, 0], edges)).toBe('right')
    expect(resolveFling([30, 2], [0.1, 0], edges)).toBeNull()
    expect(resolveFling([10, 0], [2, 0], edges)).toBeNull() // below minDistance: a tap/jitter
    expect(resolveFling([0, -30], [0, -0.8], edges)).toBe('up')
  })

  it('flicking back toward the centre cancels even past the distance', () => {
    expect(resolveFling([140, 0], [-0.8, 0], edges)).toBeNull()
    expect(resolveFling([140, 0], [-0.1, 0], edges)).toBe('right')
  })

  it('locks to the first dominant axis (up vs left/right)', () => {
    expect(lockAxis([4, 3], null)).toEqual({ axis: null, movement: [4, 3] })
    const a = lockAxis([2, -12], null)
    expect(a).toEqual({ axis: 'y', movement: [0, -12] })
    // once locked, a later sideways drift does not switch direction
    expect(lockAxis([80, -20], a.axis)).toEqual({ axis: 'y', movement: [0, -20] })
  })

  it('rubber-bands toward sides without an edge', () => {
    expect(Math.abs(rubberBand(1000))).toBeLessThan(FLING.rubberBandLimit)
    expect(rubberBand(-40)).toBeLessThan(0)
    expect(displayOffset([200, 0], edges)).toEqual([200, 0])
    const [bx] = displayOffset([200, 0], { down: edges.down })
    expect(bx).toBeGreaterThan(0)
    expect(bx).toBeLessThan(FLING.rubberBandLimit)
  })
})

describe('drag handler', () => {
  const mv = () => ({ v: 0, set(n: number) { this.v = n } })
  const st = (p: Partial<DragState>): DragState => ({
    first: false, active: false, last: false, movement: [0, 0], velocity: [0, 0], direction: [0, 0], ...p,
  })
  const setup = (opts: Partial<Parameters<typeof useFling>[0]> = {}) => {
    const x = mv()
    const y = mv()
    const onFling = vi.fn()
    const springBack = vi.fn()
    const buzz = vi.fn()
    const aimed: (string | null)[] = []
    const handle = createDragHandler({
      x, y, options: () => ({ edges, onFling, ...opts }), setAimed: (d) => aimed.push(d), springBack, haptic: buzz,
    })
    return { x, y, onFling, springBack, buzz, aimed, handle }
  }

  it('springs back under the threshold without committing', () => {
    const h = setup()
    h.handle(st({ first: true, active: true, movement: [40, 3] }))
    expect(h.x.v).toBe(40)
    expect(h.y.v).toBe(0) // axis-locked to x
    expect(h.aimed.at(-1)).toBe('right')
    h.handle(st({ last: true, movement: [50, 4], velocity: [0.05, 0], direction: [1, 0] }))
    expect(h.onFling).not.toHaveBeenCalled()
    expect(h.springBack).toHaveBeenCalledTimes(2)
    expect(h.aimed.at(-1)).toBeNull()
    expect(h.buzz).not.toHaveBeenCalled()
  })

  it('commits a velocity flick with a haptic tick', () => {
    const h = setup()
    h.handle(st({ first: true, active: true, movement: [-6, -20] }))
    // use-gesture velocity is unsigned; direction carries the sign
    h.handle(st({ last: true, movement: [-8, -32], velocity: [0.1, 1.2], direction: [-1, -1] }))
    expect(h.onFling).toHaveBeenCalledWith('up')
    expect(h.buzz).toHaveBeenCalledTimes(1)
    expect(h.springBack).not.toHaveBeenCalled()
    expect([h.x.v, h.y.v]).toEqual([0, 0])
  })

  it('a tap calls onTap instead of flinging', () => {
    const onTap = vi.fn()
    const h = setup({ onTap })
    h.handle(st({ first: true, last: true, tap: true }))
    expect(onTap).toHaveBeenCalledTimes(1)
    expect(h.onFling).not.toHaveBeenCalled()
  })

  it('does nothing while disabled', () => {
    const h = setup({ enabled: false })
    h.handle(st({ first: true, active: true, movement: [200, 0] }))
    h.handle(st({ last: true, movement: [200, 0], velocity: [2, 0], direction: [1, 0] }))
    expect(h.onFling).not.toHaveBeenCalled()
    expect(h.x.v).toBe(0)
  })
})
