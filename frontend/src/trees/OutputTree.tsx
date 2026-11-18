import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useSession } from '../store/session'
import { TreeView } from './TreeView'

export function OutputTree({ sessionId }: { sessionId: number }) {
  const version = useSession((s) => s.treeVersion)
  const { data } = useQuery({
    queryKey: ['tree', sessionId, 'output', version],
    queryFn: () => api.getTree(sessionId, 'output'),
    placeholderData: (prev) => prev,
  })
  return <TreeView root={data} emptyLabel="No folders yet" />
}
