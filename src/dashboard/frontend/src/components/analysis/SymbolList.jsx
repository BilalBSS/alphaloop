import { useState, useMemo } from 'react'
import { useApi } from '../../hooks/useApi'
import { SkeletonTable } from '../Skeleton'
import EmptyState from '../EmptyState'
import { scoreColor, consensusBadge, regimeBadge } from './formatters'

// / daily synthesis panel for list view
export function SynthesisPanel({ onSelect }) {
  const { data, loading } = useApi('/api/synthesis', 120000)

  if (loading && !data) return <SkeletonTable rows={3} cols={2} />

  if (!data || !data.date) {
    return (
      <EmptyState
        title="No synthesis yet today"
        hint="Synthesis runs after the first analyst cycle completes. Manual schedule: 5:00 PM ET after market close."
      />
    )
  }

  const buys = data.top_buys || []
  const avoids = data.top_avoids || []
  const dateStr = data.date?.split('T')[0] || data.date

  return (
    <div className="space-y-3">
      <div className="type-metric-label">
        Daily Synthesis — {dateStr} (5:00 PM ET)
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <div className="type-metric-label pnl-positive mb-2">Top Buys</div>
          {buys.length > 0 ? buys.map((b, i) => (
            <div key={i}
              onClick={() => onSelect(b.symbol || b)}
              className="flex justify-between text-xs py-0.5 cursor-pointer hover:text-accent"
            >
              <span className="font-mono">{i + 1}. {b.symbol || b}</span>
              {b.score != null && <span className="pnl-positive font-mono">+{parseFloat(b.score).toFixed(1)}</span>}
            </div>
          )) : <div className="text-text-muted text-xs">Awaiting first synthesis pass</div>}
        </div>
        <div>
          <div className="type-metric-label pnl-negative mb-2">Top Avoids</div>
          {avoids.length > 0 ? avoids.map((a, i) => (
            <div key={i}
              onClick={() => onSelect(a.symbol || a)}
              className="flex justify-between text-xs py-0.5 cursor-pointer hover:text-accent"
            >
              <span className="font-mono">{i + 1}. {a.symbol || a}</span>
              {a.score != null && <span className="pnl-negative font-mono">{parseFloat(a.score).toFixed(1)}</span>}
            </div>
          )) : <div className="text-text-muted text-xs">Awaiting first synthesis pass</div>}
        </div>
      </div>
      {data.portfolio_risk && (
        <div className="text-xs text-warning">Risk: {data.portfolio_risk}</div>
      )}
    </div>
  )
}

// / strategy evaluation cycle panel
export function StrategyEvalPanel({ onSelect }) {
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
                    onClick={() => onSelect(nm.symbol)}
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

// / symbol list view with filters + sort + sticky header
const SORT_OPTIONS = [
  { key: 'composite_desc', label: 'Composite ↓' },
  { key: 'composite_asc', label: 'Composite ↑' },
  { key: 'symbol', label: 'Symbol A-Z' },
]

export default function SymbolList({ symbols, loading, onSelect, positionSymbols = null }) {
  const [filter, setFilter] = useState('')
  const [sort, setSort] = useState('composite_desc')
  const [onlyPositions, setOnlyPositions] = useState(false)

  // / resolve positions set — accept either a prop or fall back to /api/portfolio
  const { data: portfolio } = useApi('/api/portfolio', 60000)
  const heldSet = useMemo(() => {
    if (positionSymbols) return new Set(positionSymbols)
    const pos = portfolio?.positions || []
    return new Set(pos.map(p => p.symbol))
  }, [portfolio, positionSymbols])

  const filtered = useMemo(() => {
    if (!symbols) return []
    const q = filter.toLowerCase()
    let list = symbols.filter(s => s.symbol.toLowerCase().includes(q))
    if (onlyPositions) {
      list = list.filter(s => heldSet.has(s.symbol))
    }
    // / sort
    if (sort === 'composite_desc') {
      list = [...list].sort((a, b) => (parseFloat(b.composite_score) || -Infinity) - (parseFloat(a.composite_score) || -Infinity))
    } else if (sort === 'composite_asc') {
      list = [...list].sort((a, b) => (parseFloat(a.composite_score) || Infinity) - (parseFloat(b.composite_score) || Infinity))
    } else if (sort === 'symbol') {
      list = [...list].sort((a, b) => a.symbol.localeCompare(b.symbol))
    }
    return list
  }, [symbols, filter, sort, onlyPositions, heldSet])

  if (loading) return <SkeletonTable rows={8} cols={4} />

  const heldCount = symbols ? symbols.filter(s => heldSet.has(s.symbol)).length : 0

  return (
    <div className="space-y-3">
      {/* filter row */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          placeholder="filter symbols..."
          className="flex-1 min-w-[180px] bg-bg-primary border border-border px-3 py-2 text-sm text-text-primary
            placeholder:text-text-muted outline-none focus:border-accent rounded"
        />
        <button
          onClick={() => setOnlyPositions(!onlyPositions)}
          className={`filter-chip ${onlyPositions ? 'active' : ''}`}
          title={heldSet.size === 0 ? 'no open positions' : `${heldCount} symbols currently held`}
        >
          {onlyPositions ? '✓ ' : ''}Has Position ({heldCount})
        </button>
        <div className="flex items-center gap-1">
          {SORT_OPTIONS.map(o => (
            <button
              key={o.key}
              onClick={() => setSort(o.key)}
              className={`filter-chip ${sort === o.key ? 'active' : ''}`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      <div className="relative overflow-auto max-h-[65vh] border border-border rounded">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-text-secondary text-[11px] uppercase sticky top-0 bg-bg-surface z-10 shadow-sm">
              <th className="text-left px-2 py-2">Symbol</th>
              <th className="text-right px-2 py-2">Score</th>
              <th className="text-center px-2 py-2">AI</th>
              <th className="text-center px-2 py-2">Regime</th>
              <th className="text-center px-2 py-2">Held</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(s => {
              const held = heldSet.has(s.symbol)
              return (
                <tr
                  key={s.symbol}
                  onClick={() => onSelect(s.symbol)}
                  className={`hover:bg-bg-hover border-t border-border cursor-pointer ${held ? 'bg-bg-primary/40' : ''}`}
                  style={{ height: 36 }}
                >
                  <td className="px-2 py-1 font-mono font-semibold">{s.symbol}</td>
                  <td className={`px-2 py-1 text-right font-mono ${s.composite_score == null ? 'text-text-muted' : scoreColor(s.composite_score)}`}>
                    {s.composite_score == null ? '—' : parseFloat(s.composite_score).toFixed(1)}
                  </td>
                  <td className="px-2 py-1 text-center">{s.ai_consensus ? consensusBadge(s.ai_consensus) : <span className="text-text-muted">—</span>}</td>
                  <td className="px-2 py-1 text-center">{s.regime ? regimeBadge(s.regime) : <span className="text-text-muted">—</span>}</td>
                  <td className="px-2 py-1 text-center">
                    {held ? <span className="chip chip-accent">held</span> : <span className="text-text-muted text-[10px]">—</span>}
                  </td>
                </tr>
              )
            })}
            {filtered.length === 0 && (
              <tr><td colSpan={5} className="text-text-muted text-sm py-6 text-center">
                {onlyPositions && heldCount === 0
                  ? 'No open positions — toggle "Has Position" off to see the full universe'
                  : `No symbols match "${filter}"`}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
