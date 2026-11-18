import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { FolderOut } from '../api/types'
import { filterFolders } from '../lib/fuzzy'
import { FolderPicker } from './FolderPicker'

const f = (id: number, path: string): FolderOut => ({
  id,
  path,
  name: path.split('/').pop() ?? '',
  depth: path ? path.split('/').length : 0,
  created_by: 'scan',
})
const folders = [f(1, ''), f(2, 'Documents'), f(3, 'Documents/Receipts'), f(4, 'Photos/Trips')]

describe('fuzzy filter', () => {
  it('matches subsequences and ranks tighter matches first', () => {
    const r = filterFolders(folders, 'rcpt').map((x) => x.id)
    expect(r).toEqual([3])
    expect(filterFolders(folders, 'doc').map((x) => x.id)[0]).toBe(2)
    expect(filterFolders(folders, 'zzz')).toEqual([])
  })
})

describe('FolderPicker', () => {
  it('filters while typing and Enter selects the top result', async () => {
    const onPick = vi.fn()
    render(<FolderPicker folders={folders} onPick={onPick} onClose={vi.fn()} onCreate={vi.fn()} />)
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Search folders'), 'trip')
    expect(screen.getAllByRole('option')[0]).toHaveTextContent('Photos/Trips')
    await user.keyboard('{Enter}')
    expect(onPick).toHaveBeenCalledWith(folders[3])
  })

  it('navigates with arrow keys and closes with Escape', async () => {
    const onPick = vi.fn()
    const onClose = vi.fn()
    render(<FolderPicker folders={folders} onPick={onPick} onClose={onClose} onCreate={vi.fn()} />)
    const user = userEvent.setup()
    await user.keyboard('{ArrowDown}{ArrowDown}{Enter}')
    expect(onPick).toHaveBeenCalledWith(folders[2])
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('creates a new folder under the selected parent and picks it', async () => {
    const created = f(9, 'Documents/Taxes')
    const onCreate = vi.fn().mockResolvedValue(created)
    const onPick = vi.fn()
    render(<FolderPicker folders={folders} onPick={onPick} onClose={vi.fn()} onCreate={onCreate} />)
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Search folders'), 'docu')
    await user.click(screen.getByText('+ New folder'))
    await user.type(screen.getByLabelText('New folder name'), 'Taxes{Enter}')
    expect(onCreate).toHaveBeenCalledWith(2, 'Taxes')
    expect(onPick).toHaveBeenCalledWith(created)
  })
})
