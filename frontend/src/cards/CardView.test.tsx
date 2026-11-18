import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Card } from '../api/types'
import { CardView } from './CardView'

vi.mock('../api/client', () => ({ fetchPreview: () => Promise.resolve(null) }))

const card: Card = {
  id: 'm1',
  kind: 'manual',
  reason: '',
  file: { id: 1, name: 'invoice.pdf', rel_path: 'a/invoice.pdf', size: 2048, mtime: 1_700_000_000, ext: '.pdf', is_bundle: false },
  edges: {
    left: { label: 'Elsewhere', action: 'picker' },
    up: { label: 'Snooze', action: 'defer' },
    down: { label: 'Bin it', action: 'trash' },
  },
}

describe('CardView', () => {
  it('renders edge labels from card.edges and skips missing edges', () => {
    const { container } = render(<CardView card={card} />)
    expect(screen.getByText('Elsewhere')).toBeInTheDocument()
    expect(screen.getByText('Snooze')).toBeInTheDocument()
    expect(screen.getByText('Bin it')).toBeInTheDocument()
    expect(container.querySelector('[data-edge="right"]')).toBeNull()
    expect(container.querySelectorAll('[data-edge]')).toHaveLength(3)
  })

  it('shows file details and highlights the aimed edge', () => {
    const { container } = render(<CardView card={card} aimed="down" />)
    expect(screen.getByText('invoice.pdf')).toBeInTheDocument()
    expect(screen.getByText('a/invoice.pdf')).toBeInTheDocument()
    expect(container.querySelector('[data-edge="down"]')).toHaveAttribute('data-aimed', 'true')
    expect(container.querySelector('[data-edge="up"]')).not.toHaveAttribute('data-aimed')
  })
})
