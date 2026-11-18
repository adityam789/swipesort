import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { BatchInfo, Card, DuplicateInfo, FileInfo } from '../api/types'
import { CardView } from './CardView'

const fetchPreview = vi.hoisted(() => vi.fn(() => Promise.resolve(null)))
vi.mock('../api/client', () => ({ fetchPreview }))

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

  describe('duplicate card', () => {
    const dupInfo: DuplicateInfo = {
      relation: 'near_image',
      similarity: 0.94,
      other_name: 'invoice (1).pdf',
      other_rel_path: 'Finance/invoice (1).pdf',
      other_side: 'output',
      other_size: 4096,
      other_mtime: 1_700_000_000,
      other_preview_url: '/api/files/99/preview',
      recommend: 'keep_this',
      reason: 'Near-identical to Finance/invoice (1).pdf (94%)',
    }
    const dup: Card = {
      ...card,
      id: 'd1',
      kind: 'duplicate',
      duplicate: dupInfo,
      edges: {
        right: { label: 'Keep both', action: 'keep_both' },
        up: { label: 'Keep this, trash the other', action: 'keep_this' },
        left: { label: 'Details', action: 'details' },
        down: { label: 'Trash this one', action: 'trash' },
      },
    }

    it('renders both previews, names and a relation chip with similarity', () => {
      fetchPreview.mockClear()
      render(<CardView card={dup} />)
      expect(screen.getByTestId('relation-chip')).toHaveTextContent('Near')
      expect(screen.getByTestId('relation-chip')).toHaveTextContent('94%')
      expect(screen.getByTestId('relation-chip')).toHaveAttribute('data-relation', 'near_image')
      expect(screen.getByTestId('dup-side-this')).toHaveTextContent('invoice.pdf')
      expect(screen.getByTestId('dup-side-other')).toHaveTextContent('invoice (1).pdf')
      const sources = fetchPreview.mock.calls.map((c) => (c as unknown[])[0])
      expect(sources).toContain(1)
      expect(sources).toContain('/api/files/99/preview')
    })

    it.each([
      ['keep_this', 'up'],
      ['trash_this', 'down'],
      ['keep_both', 'right'],
    ] as const)('highlights the %s recommended edge (%s) from data', (recommend, dir) => {
      const { container } = render(<CardView card={{ ...dup, duplicate: { ...dupInfo, recommend } }} />)
      const highlighted = [...container.querySelectorAll('[data-highlight]')].map((e) => e.getAttribute('data-edge'))
      expect(highlighted).toEqual([dir])
    })

    it('supports a generic edge.highlight flag on any card', () => {
      const c: Card = { ...card, edges: { ...card.edges, down: { label: 'Bin it', action: 'trash', highlight: true } } }
      const { container } = render(<CardView card={c} />)
      expect(container.querySelector('[data-edge="down"]')).toHaveAttribute('data-highlight', 'true')
      expect(container.querySelector('[data-edge="up"]')).not.toHaveAttribute('data-highlight')
    })
  })
  describe('batch card', () => {
    const f = (id: number): FileInfo => ({ id, name: `tax-${id}.pdf`, rel_path: `in/tax-${id}.pdf`, size: 10, mtime: 0, ext: '.pdf', is_bundle: false })
    const batchInfo: BatchInfo = {
      kind: 'move',
      target_folder_id: 7,
      target_path: 'Finance/Taxes/2025',
      members: Array.from({ length: 14 }, (_, i) => f(i + 1)),
      min_confidence: 0.953,
    }
    const batch: Card = {
      ...card,
      id: 'b1',
      kind: 'batch',
      batch: batchInfo,
      file: batchInfo.members[0],
      edges: {
        right: { label: 'Confirm all', action: 'confirm_all' },
        up: { label: 'Review', action: 'review' },
        left: { label: 'Reject all', action: 'reject_all' },
        down: { label: 'Trash all', action: 'trash_all' },
      },
    }

    it('renders count, target, min confidence, members and thumbs', () => {
      const { container } = render(<CardView card={batch} />)
      expect(screen.getByTestId('batch-count')).toHaveTextContent('14')
      expect(screen.getByTestId('batch-target')).toHaveTextContent('Finance/Taxes/2025')
      expect(screen.getByTestId('batch-min-confidence')).toHaveTextContent('95%')
      expect(screen.getAllByTestId('batch-member')).toHaveLength(14)
      expect(screen.getByText('tax-14.pdf')).toBeInTheDocument()
      expect(screen.getAllByTestId('batch-thumb')).toHaveLength(6)
      expect(container.querySelector('img')).toHaveAttribute('src', '/api/files/1/preview')
      expect(screen.getByText('Confirm all')).toBeInTheDocument()
      expect(screen.getByText('Trash all')).toBeInTheDocument()
    })

    it('uses the inbox wording for inbox batches', () => {
      const inbox: Card = { ...batch, batch: { ...batchInfo, kind: 'inbox', target_path: 'Work/_Inbox', members: batchInfo.members.slice(0, 7) } }
      render(<CardView card={inbox} />)
      expect(screen.getByTestId('batch-summary')).toHaveTextContent("Couldn't place these 7 files — send them to Work/_Inbox/?")
    })
  })

  describe('reason and destination', () => {
    it('shows the reason prominently and the destination', () => {
      const c: Card = {
        ...card,
        kind: 'inbox',
        reason: 'Probably somewhere in Work/PayPal/, unsure below that.',
        destination: 'Work/PayPal/_Inbox',
      }
      render(<CardView card={c} />)
      expect(screen.getByTestId('reason')).toHaveTextContent('Probably somewhere in Work/PayPal/, unsure below that.')
      expect(screen.getByTestId('destination')).toHaveTextContent('Work/PayPal/_Inbox')
    })

    it('omits destination when absent', () => {
      render(<CardView card={card} />)
      expect(screen.queryByTestId('destination')).toBeNull()
    })
  })
})
