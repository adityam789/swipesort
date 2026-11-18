import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Card } from '../api/types'
import { CardView } from './CardView'

vi.mock('../api/client', () => ({ fetchPreview: () => Promise.resolve(null) }))

const card: Card = {
  id: 'm1',
  kind: 'manual',
  reason: '',
  candidates: [],
  keys: {},
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

  it('shows kind chip, confidence, reason, candidates and key hints', () => {
    const pick = { label: 'Receipts', action: 'pick', target_folder_id: 3 } as const
    const rich: Card = {
      ...card,
      kind: 'choices',
      reason: 'matches 23 files',
      confidence: 0.72,
      candidates: [
        { folder_id: 3, path: 'Receipts', p: 0.72 },
        { folder_id: 4, path: 'Taxes/2025', p: 0.2 },
      ],
      edges: { right: pick },
      keys: { '1': pick },
    }
    render(<CardView card={rich} />)
    expect(screen.getByTestId('kind-chip')).toHaveAttribute('data-kind', 'choices')
    expect(screen.getByTestId('confidence')).toHaveTextContent('72%')
    expect(screen.getByText('matches 23 files')).toBeInTheDocument()
    expect(screen.getByTestId('candidates')).toHaveTextContent('Taxes/2025')
    expect(screen.getByText('1')).toBeInTheDocument()
  })
})
