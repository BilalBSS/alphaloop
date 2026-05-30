import { useApi } from '../../hooks/useApi'
import EmptyState from '../EmptyState'
import { scoreColor } from './formatters'
import { clickableProps } from '../ui/clickable'

// / latest strategy evaluation cycle
export default function StrategyEvalPanel({ onSelect }) {
  const { data, loading } = useApi('/api/strategy-evaluations?limit=1', 120000)

  if (loading && !data) return <div className="text-text-muted text-sm py-2">Loading...</div>

  const latest = Array.isArray(data) && data.length > 0 ? data[0] : null
  if (!latest) {
    return (
      <EmptyState
        title="No evaluation cycles yet"
        hint="Strategy agent runs every 15 minutes during market hours and logs a cycle row here each pass."
      />
    )
  }

  const nearMisses = latest.near_misses || []
  const ts = latest.created_at?.split('T')[1]?.slice(0, 5) || ''

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-4 text-xs font-mono">
        <span>{latest.total_pairs} pairs</span>
        <span className="pnl-positive">{latest.entry_hits} hits</span>
        <span className="pnl-negative">{latest.blocked_consensus} consensus</span>
        <span className="text-warning">{latest.blocked_threshold} threshold</span>
        <span className={latest.signals_generated > 0 ? 'pnl-positive font-bold' : ''}>{latest.signals_generated} signals</span>
        {ts && <span className="text-text-muted">{ts} UTC</span>}
      </div>
      {nearMisses.length > 0 && (
        <div>
          <div className="type-metric-label mb-2">Near-Misses</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-secondary text-[11px] uppercase">
                <th className="text-left px-2 py-1">Symbol</th>
                <th className="text-right px-2 py-1">Strength</th>
                <th className="text-left px-2 py-1">Block</th>
              </tr>
            </thead>
            <tbody>
              {nearMisses.map((nm, i) => {
                const isConsensus = (nm.block_reason || '').includes('consensus')
                return (
                  <tr
                    key={i}
                    {...clickableProps(() => onSelect(nm.symbol))}
                    className={`border-t border-border hover:bg-bg-hover cursor-pointer border-l-2 ${isConsensus ? 'border-l-loss' : 'border-l-warning'}`}
                    style={{ height: 32 }}
                  >
                    <td className="px-2 py-1 font-mono font-semibold">{nm.symbol}</td>
                    <td className={`px-2 py-1 text-right font-mono ${scoreColor(nm.raw_strength * 100)}`}>
                      {parseFloat(nm.raw_strength || 0).toFixed(2)}
                    </td>
                    <td className="px-2 py-1">
                      <span className={`chip ${isConsensus ? 'chip-negative' : 'chip-warning'}`}>
                        {isConsensus ? 'consensus' : 'threshold'}
                      </span>
                    </td>
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
