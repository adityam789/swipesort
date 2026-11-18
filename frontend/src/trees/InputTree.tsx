import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useSession } from '../store/session'
import { TreeView } from './TreeView'

export function InputTree({ sessionId }: { sessionId: number }) {
  const version = useSession((s) => s.treeVersion)
  const { data } = useQuery({
    queryKey: ['tree', sessionId, 'input', version],
    queryFn: () => api.getTree(sessionId, 'input'),
    placeholderData: (prev) => prev,
  })
  return <TreeView root={data} emptyLabel="Nothing left to sort" />
}
