import { useState, useMemo } from 'react'
import { useApi } from '../../hooks/useApi'
import { SkeletonTable } from '../Skeleton'
import { scoreColor, consensusBadge, regimeBadge } from './formatters'

// / daily synthesis panel for list view
export function SynthesisPanel({ onSelect }) {
  const { data, loading } = useApi('/api/synthesis', 120000)

  if (loading && !data) return <SkeletonTable rows={3} cols={2} />

  if (!data || !data.date) {
    return <div className="text-text-muted text-sm py-2">No synthesis yet. The reasoner runs daily at 5PM ET.</div>
  }

  const buys = data.top_buys || []
  const avoids = data.top_avoids || []
  const dateStr = data.date?.split('T')[0] || data.date

  return (
    <div className="space-y-3">
      <div className="text-[11px] uppercase text-text-secondary">
        Daily Synthesis — {dateStr} (5:00 PM ET)
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <div className="text-[11px] uppercase text-profit mb-1">Top Buys</div>
          {buys.length > 0 ? buys.map((b, i) => (
            <div key={i}
              onClick={() => onSelect(b.symbol || b)}
              className="flex justify-between text-xs py-0.5 cursor-pointer hover:text-accent"
            >
              <span className="font-mono">{i + 1}. {b.symbol || b}</span>
              {b.score != null && <span className="text-profit font-mono">+{parseFloat(b.score).toFixed(1)}</span>}
            </div>
          )) : <div className="text-text-muted text-xs">--</div>}
        </div>
        <div>
          <div className="text-[11px] uppercase text-loss mb-1">Top Avoids</div>
          {avoids.length > 0 ? avoids.map((a, i) => (
            <div key={i}
              onClick={() => onSelect(a.symbol || a)}
              className="flex justify-between text-xs py-0.5 cursor-pointer hover:text-accent"
            >
              <span className="font-mono">{i + 1}. {a.symbol || a}</span>
              {a.score != null && <span className="text-loss font-mono">{parseFloat(a.score).toFixed(1)}</span>}
            </div>
          )) : <div className="text-text-muted text-xs">--</div>}
        </div>
      </div>
      {data.portfolio_risk && (
        <div className="text-xs text-warning">Risk: {data.portfolio_risk}</div>
      )}
    </div>
  )
}

// / strategy evaluation cycle panel (collapsed by default)
export function StrategyEvalPanel({ onSelect }) {
  const { data, loading } = useApi('/api/strategy-evaluations?limit=1', 120000)

  if (loading && !data) return <div className="text-text-muted text-sm py-2">Loading...</div>

  const latest = Array.isArray(data) && data.length > 0 ? data[0] : null
  if (!latest) return <div className="text-text-muted text-sm py-2">No evaluation data yet. Strategy agent posts every 5 min.</div>

  const nearMisses = latest.near_misses || []
  const ts = latest.created_at?.split('T')[1]?.slice(0, 5) || ''

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-4 text-xs font-mono">
        <span>{latest.total_pairs} pairs</span>
        <span className="text-profit">{latest.entry_hits} hits</span>
        <span className="text-loss">{latest.blocked_consensus} consensus</span>
        <span className="text-warning">{latest.blocked_threshold} threshold</span>
        <span className={latest.signals_generated > 0 ? 'text-profit font-bold' : ''}>{latest.signals_generated} signals</span>
        {ts && <span className="text-text-muted">{ts} UTC</span>}
      </div>
      {nearMisses.length > 0 && (
        <div>
          <div className="text-[11px] uppercase text-text-secondary mb-1">Near-Misses</div>
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
                    onClick={() => onSelect(nm.symbol)}
                    className={`border-t border-border hover:bg-bg-hover cursor-pointer border-l-2 ${isConsensus ? 'border-l-loss' : 'border-l-warning'}`}
                    style={{ height: 32 }}
                  >
                    <td className="px-2 py-1 font-mono font-semibold">{nm.symbol}</td>
                    <td className={`px-2 py-1 text-right font-mono ${scoreColor(nm.raw_strength * 100)}`}>
                      {parseFloat(nm.raw_strength || 0).toFixed(2)}
                    </td>
                    <td className="px-2 py-1">
                      <span className={`px-2 py-0.5 text-xs font-semibold uppercase ${isConsensus ? 'text-loss' : 'text-warning'}`}>
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

// / symbol list view
export default function SymbolList({ symbols, loading, onSelect }) {
  const [filter, setFilter] = useState('')

  const filtered = useMemo(() => {
    if (!symbols) return []
    const q = filter.toLowerCase()
    return symbols.filter(s => s.symbol.toLowerCase().includes(q))
  }, [symbols, filter])

  if (loading) return <SkeletonTable rows={8} cols={4} />

  return (
    <div>
      <input
        type="text"
        value={filter}
        onChange={e => setFilter(e.target.value)}
        placeholder="filter symbols..."
        className="w-full bg-bg-primary border border-border px-3 py-2 text-sm text-text-primary
          placeholder:text-text-muted mb-2 outline-none focus:border-accent"
      />
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary text-[11px] uppercase">
            <th className="text-left px-2 py-1">Symbol</th>
            <th className="text-right px-2 py-1">Score</th>
            <th className="text-center px-2 py-1">AI</th>
            <th className="text-center px-2 py-1">Regime</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map(s => (
            <tr
              key={s.symbol}
              onClick={() => onSelect(s.symbol)}
              className="hover:bg-bg-hover border-t border-border cursor-pointer"
              style={{ height: 36 }}
            >
              <td className="px-2 py-1 font-mono font-semibold">{s.symbol}</td>
              <td className={`px-2 py-1 text-right font-mono ${scoreColor(s.composite_score)}`}>
                {parseFloat(s.composite_score || 0).toFixed(1)}
              </td>
              <td className="px-2 py-1 text-center">{consensusBadge(s.ai_consensus)}</td>
              <td className="px-2 py-1 text-center">{regimeBadge(s.regime)}</td>
            </tr>
          ))}
          {filtered.length === 0 && (
            <tr><td colSpan={4} className="text-text-muted text-sm py-4 text-center">No symbols match</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
