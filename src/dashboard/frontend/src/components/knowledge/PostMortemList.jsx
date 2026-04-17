import { useState } from 'react'
import { useApi } from '../../hooks/useApi'

export default function PostMortemList() {
  const { data, loading } = useApi('/api/post-mortems?limit=100', 60000)
  const [expandedId, setExpandedId] = useState(null)

  if (loading) return <div className="text-text-muted text-sm py-8">loading…</div>
  if (!data || data.length === 0) {
    return <div className="text-text-muted text-sm py-8">No post-mortems yet. They trigger on closed losses &gt; $50 or &gt; 2%.</div>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary text-[11px] uppercase">
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
            const pnlColor = pnl == null ? 'text-text-muted' : pnl < 0 ? 'text-loss' : 'text-profit'
            const open = expandedId === pm.id
            return (
              <>
                <tr
                  key={pm.id}
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
                  <tr key={`${pm.id}-detail`} className="bg-bg-primary/70">
                    <td colSpan={7} className="px-3 py-2">
                      <pre className="text-[11px] text-text-secondary whitespace-pre-wrap">
                        {typeof pm.details === 'string' ? pm.details : JSON.stringify(pm.details, null, 2)}
                      </pre>
                    </td>
                  </tr>
                )}
              </>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
