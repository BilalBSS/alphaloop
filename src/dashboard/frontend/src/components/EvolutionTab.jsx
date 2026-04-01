import Panel from './Panel'
import { SkeletonTable } from './Skeleton'

export default function EvolutionTab({ evolution, loading }) {
  if (loading) {
    return <Panel title="Evolution Log"><SkeletonTable rows={6} cols={5} /></Panel>
  }

  return (
    <Panel title="Evolution Log">
      {!evolution || evolution.length === 0 ? (
        <div className="text-text-muted text-sm py-8">
          Evolution engine hasn't run yet
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-secondary text-[11px] uppercase">
                <th className="text-left px-2 py-1">Gen</th>
                <th className="text-left px-2 py-1">Action</th>
                <th className="text-left px-2 py-1">Strategy</th>
                <th className="text-left px-2 py-1">Parent</th>
                <th className="text-left px-2 py-1">Reason</th>
                <th className="text-right px-2 py-1">Date</th>
              </tr>
            </thead>
            <tbody>
              {evolution.map((e, i) => {
                const actionColor = {
                  kill: 'text-loss',
                  mutate: 'text-accent',
                  promote: 'text-profit',
                }[e.action] || 'text-text-secondary'

                return (
                  <tr key={i} className="hover:bg-bg-hover border-t border-border" style={{ height: 36 }}>
                    <td className="px-2 py-1 font-mono">{e.generation}</td>
                    <td className={`px-2 py-1 font-semibold ${actionColor}`}>
                      {e.action?.toUpperCase()}
                    </td>
                    <td className="px-2 py-1 truncate max-w-[120px]">{e.strategy_id}</td>
                    <td className="px-2 py-1 text-text-muted truncate max-w-[100px]">{e.parent_id || '--'}</td>
                    <td className="px-2 py-1 text-text-secondary truncate max-w-[200px]">{e.reason || '--'}</td>
                    <td className="px-2 py-1 text-right text-text-muted">{e.created_at?.replace('T', ' ').slice(0, 16) || '--'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
