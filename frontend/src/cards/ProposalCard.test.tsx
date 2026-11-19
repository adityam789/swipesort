import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Card, FileInfo, TaxonomyNode } from '../api/types'
import { allNodeIds, toggleNode } from '../lib/proposal'
import { useSession } from '../store/session'
import { CardView } from './CardView'

const fetchPreview = vi.hoisted(() => vi.fn(() => Promise.resolve(null)))
vi.mock('../api/client', () => ({ fetchPreview, api: {} }))

const file = (id: number): FileInfo => ({ id, name: `cv_${id}.pdf`, rel_path: `cv_${id}.pdf`, size: 1, mtime: 0, ext: '.pdf', is_bundle: false })

const proposal: Card = {
  id: 'p7',
  kind: 'folder_proposal',
  reason: 'Create Work/Resumes/ for these 3 files?',
  candidates: [],
  keys: {},
  file: file(1),
  destination: 'Work/Resumes',
  edges: {
    right: { label: 'Create Work/Resumes/ & move 3 files', action: 'accept' },
    up: { label: 'Send 3 to Work/_Inbox/', action: 'inbox' },
    left: { label: 'Dismiss', action: 'dismiss' },
  },
  proposal: {
    proposal_id: 7,
    name: 'Resumes',
    parent_folder_id: 2,
    parent_path: 'Work',
    path: 'Work/Resumes',
    inbox_path: 'Work/_Inbox',
    members: [file(1), file(2), file(3)],
    cohesion: 0.82,
    singleton: false,
    named_by: 'llm',
  },
}

const nodes: TaxonomyNode[] = [
  { id: '0', name: 'Finance', count: 30, sample: ['inv.pdf'], children: [
    { id: '0.0', name: 'Invoices', count: 20, sample: [], children: [] },
    { id: '0.1', name: 'Receipts', count: 10, sample: [], children: [] },
  ] },
  { id: '1', name: 'Photos', count: 12, sample: [], children: [] },
]

const taxonomy: Card = {
  ...proposal,
  id: 'p8',
  kind: 'taxonomy',
  proposal: null,
  reason: 'Your output folder is empty.',
  edges: { right: { label: 'Create these folders', action: 'accept' }, left: { label: 'Dismiss', action: 'dismiss' } },
  taxonomy: { proposal_id: 8, nodes, inbox_count: 2, total: 44, named_by: 'fallback' },
}

beforeEach(() => useSession.setState({ drafts: {} }))

describe('ProposalCard', () => {
  it('shows the proposed path, members, edges as data, and edits the name into a draft', () => {
    const { container } = render(<CardView card={proposal} />)
    expect(screen.getByTestId('kind-chip')).toHaveTextContent('new folder')
    expect(screen.getAllByTestId('proposal-member')).toHaveLength(3)
    expect(screen.getByTestId('named-by')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-edge]')).toHaveLength(3)
    expect(container.querySelector('[data-edge="down"]')).toBeNull()
    const input = screen.getByTestId('proposal-name') as HTMLInputElement
    expect(input.value).toBe('Resumes')
    fireEvent.change(input, { target: { value: 'CVs' } })
    expect(useSession.getState().drafts.p7?.name).toBe('CVs')
    expect((screen.getByTestId('proposal-name') as HTMLInputElement).value).toBe('CVs')
  })
})

describe('TaxonomyCard', () => {
  it('lists the tree with checkboxes and editable names', () => {
    render(<CardView card={taxonomy} />)
    expect(screen.getAllByTestId('taxonomy-node')).toHaveLength(4)
    expect(screen.getByTestId('taxonomy-selected')).toHaveTextContent('4 of 4')
    fireEvent.click(screen.getByLabelText('Create Finance'))
    expect(useSession.getState().drafts.p8?.accepted_nodes).toEqual(['1'])
    expect(screen.getByTestId('taxonomy-selected')).toHaveTextContent('1 of 4')
    fireEvent.change(screen.getByLabelText('Name of Photos'), { target: { value: 'Pictures' } })
    expect(useSession.getState().drafts.p8?.renames).toEqual({ '1': 'Pictures' })
  })

  it('toggleNode keeps parents and children consistent', () => {
    const all = allNodeIds(nodes)
    expect(all).toEqual(['0', '0.0', '0.1', '1'])
    expect(toggleNode(nodes, all, '0')).toEqual(['1'])
    expect(toggleNode(nodes, ['1'], '0.1')).toEqual(['0', '0.1', '1']) // child brings its parent
    expect(toggleNode(nodes, all, '0.0')).toEqual(['0', '0.1', '1'])
  })
})
