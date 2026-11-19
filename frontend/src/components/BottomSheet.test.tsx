import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SHEET } from '../gestures/constants'
import { shouldDismissSheet } from '../gestures/sheet'
import { setMobile } from '../test/media'
import { BottomSheet } from './BottomSheet'

afterEach(() => vi.unstubAllGlobals())

describe('shouldDismissSheet', () => {
  it('dismisses past the distance or on a fast downward flick', () => {
    expect(shouldDismissSheet(SHEET.dismissDistance, 0)).toBe(true)
    expect(shouldDismissSheet(30, SHEET.dismissVelocity + 1)).toBe(true)
    expect(shouldDismissSheet(30, 50)).toBe(false) // springs back
    expect(shouldDismissSheet(-20, 2000)).toBe(false) // flicked up
  })
})

describe('BottomSheet', () => {
  it('is a draggable bottom sheet with a handle on phones', () => {
    setMobile(true)
    render(
      <BottomSheet label="Output tree" title="Output" onClose={() => undefined}>
        <p>body</p>
      </BottomSheet>,
    )
    const dialog = screen.getByRole('dialog', { name: 'Output tree' })
    expect(dialog).toHaveAttribute('data-sheet', 'true')
    expect(screen.getByTestId('sheet-handle')).toBeInTheDocument()
    expect(dialog).toHaveTextContent('Output')
    expect(screen.getByTestId('sheet-close')).toBeInTheDocument()
    expect(screen.getByText('body')).toBeInTheDocument()
  })

  it('is a centred modal on desktop', () => {
    setMobile(false)
    render(
      <BottomSheet label="Choose" onClose={() => undefined}>
        <p>body</p>
      </BottomSheet>,
    )
    expect(screen.getByRole('dialog', { name: 'Choose' })).not.toHaveAttribute('data-sheet')
    expect(screen.queryByTestId('sheet-handle')).toBeNull()
  })

  it('closes on backdrop tap and Escape (unless the content handles Escape)', async () => {
    setMobile(true)
    const onClose = vi.fn()
    const { unmount } = render(
      <BottomSheet label="S" onClose={onClose}>
        <p>body</p>
      </BottomSheet>,
    )
    fireEvent.mouseDown(screen.getByText('body'))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.mouseDown(screen.getByTestId('sheet-backdrop'))
    expect(onClose).toHaveBeenCalledTimes(1)
    await userEvent.setup().keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(2)
    unmount()

    const onClose2 = vi.fn()
    render(
      <BottomSheet label="S" onClose={onClose2} closeOnEscape={false}>
        <p>body</p>
      </BottomSheet>,
    )
    await userEvent.setup().keyboard('{Escape}')
    expect(onClose2).not.toHaveBeenCalled()
  })
})
