import { useApi } from '../../hooks/useApi'
import { clickableProps } from '../ui/clickable'

// / n-1 of n near-misses
export default function ObservationLogPanel({ onSelect }) {
  const { data } = useApi('/api/observation-log?hours=24&limit=30', 60000)
  if (!data) return null
  const rows = Array.isArray(data.by_strategy) ? data.by_strategy : []
  const recent = Array.isArray(data.recent) ? data.recent : []
  if (rows.length === 0 && recent.length === 0) {
    return (
      <div className="text-xs text-text-muted py-2">
        No near-misses in the last 24h. Once a strategy passes N-1 of N entry clauses (or the fundamental gate is the only blocker) it appears here.
      </div>
    )
  }
  return (
    <div className="space-y-3">
      {rows.length > 0 && (
        <div>
          <div className="type-metric-label mb-2">Close-to-firing strategies (24h)</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-secondary text-[11px] uppercase">
                <th className="text-left px-2 py-1">Strategy</th>
                <th className="text-right px-2 py-1">Near-misses</th>
                <th className="text-right px-2 py-1">N-1 technical</th>
                <th className="text-right px-2 py-1">Fund gate</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 15).map((r, i) => (
                <tr key={i} className="border-t border-border" style={{ height: 28 }}>
                  <td className="px-2 py-1 font-mono text-text-primary">{r.strategy_id}</td>
                  <td className="px-2 py-1 text-right font-mono">{r.total}</td>
                  <td className="px-2 py-1 text-right font-mono text-warning">{r.n_minus_1}</td>
                  <td className="px-2 py-1 text-right font-mono text-text-muted">{r.fundamental_gate}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {recent.length > 0 && (
        <div>
          <div className="type-metric-label mb-2">Recent near-misses</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-secondary text-[11px] uppercase">
                <th className="text-left px-2 py-1">Time</th>
                <th className="text-left px-2 py-1">Strategy</th>
                <th className="text-left px-2 py-1">Symbol</th>
                <th className="text-left px-2 py-1">Passed</th>
                <th className="text-left px-2 py-1">Reason</th>
              </tr>
            </thead>
            <tbody>
              {recent.slice(0, 15).map((r, i) => {
                const ts = r.created_at?.split('T')[1]?.slice(0, 5) || ''
                const frac = r.total_count > 0 ? `${r.passed_count}/${r.total_count}` : '—'
                return (
                  <tr
                    key={i}
                    {...(onSelect ? clickableProps(() => onSelect(r.symbol)) : {})}
                    className="border-t border-border hover:bg-bg-hover cursor-pointer"
                    style={{ height: 28 }}
                  >
                    <td className="px-2 py-1 text-text-muted font-mono">{ts}</td>
                    <td className="px-2 py-1 font-mono text-text-primary truncate max-w-[110px]" title={r.strategy_id}>{r.strategy_id}</td>
                    <td className="px-2 py-1 font-mono">{r.symbol}</td>
                    <td className="px-2 py-1 font-mono text-text-muted">{frac}</td>
                    <td className="px-2 py-1 text-text-secondary truncate max-w-[280px]" title={r.failed_reason}>{r.failed_reason}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
