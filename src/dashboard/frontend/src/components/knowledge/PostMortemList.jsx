import { useState, Fragment } from 'react'
import { useApi } from '../../hooks/useApi'
import EmptyState from '../EmptyState'

export default function PostMortemList() {
  const { data, loading } = useApi('/api/post-mortems?limit=100', 60000)
  const [expandedId, setExpandedId] = useState(null)

  if (loading) return <div className="text-text-muted text-sm py-8">loading…</div>
  if (!data || data.length === 0) {
    return (
      <EmptyState
        title="No post-mortems yet"
        hint="Writes on first closed loss > $50 or > 2% — an automatic retrospective of what went wrong."
      />
    )
  }

  return (
    <div className="overflow-x-auto table-scroll-fade">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary text-[11px] uppercase sticky top-0 bg-bg-surface">
            <th className="text-left px-2 py-1">When</th>
            <th className="text-left px-2 py-1">Strategy</th>
            <th className="text-left px-2 py-1">Symbol</th>
            <th className="text-left px-2 py-1">Trigger</th>
            <th className="text-right px-2 py-1">P&amp;L</th>
            <th className="text-right px-2 py-1">Sigma</th>
            <th className="text-left px-2 py-1">Wiki</th>
          </tr>
        </thead>
        <tbody>
          {data.map((pm) => {
            const pnl = pm.pnl != null ? Number(pm.pnl) : null
            const pnlColor = pnl == null ? 'text-text-muted' : pnl < 0 ? 'pnl-negative' : 'pnl-positive'
            const open = expandedId === pm.id
            return (
              <Fragment key={pm.id}>
                <tr
                  onClick={() => setExpandedId(open ? null : pm.id)}
                  className="border-b border-border/50 hover:bg-bg-primary/50 cursor-pointer"
                >
                  <td className="px-2 py-1.5 text-text-muted">
                    {pm.created_at ? new Date(pm.created_at).toLocaleString() : '—'}
                  </td>
                  <td className="px-2 py-1.5 font-mono">{pm.strategy_id}</td>
                  <td className="px-2 py-1.5 font-mono">{pm.symbol}</td>
                  <td className="px-2 py-1.5 text-text-secondary">{pm.trigger_type}</td>
                  <td className={`px-2 py-1.5 text-right font-mono ${pnlColor}`}>
                    {pnl != null ? `$${pnl.toFixed(2)}` : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-text-muted">
                    {pm.deviation_sigma != null ? Number(pm.deviation_sigma).toFixed(2) : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-text-muted font-mono truncate max-w-[200px]">
                    {pm.wiki_path || '—'}
                  </td>
                </tr>
                {open && pm.details && (
                  <tr className="bg-bg-primary/70">
                    <td colSpan={7} className="px-3 py-2">
                      <pre className="text-[11px] text-text-secondary whitespace-pre-wrap">
                        {typeof pm.details === 'string' ? pm.details : JSON.stringify(pm.details, null, 2)}
                      </pre>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
