import { useState, useMemo } from 'react'
import { useApi } from '../../hooks/useApi'
import { SkeletonTable } from '../Skeleton'
import { scoreColor, consensusBadge, regimeBadge } from './formatters'
import { clickableProps } from '../ui/clickable'

// / symbol list view
const SORT_OPTIONS = [
  { key: 'composite_desc', label: 'Composite ↓' },
  { key: 'composite_asc', label: 'Composite ↑' },
  { key: 'symbol', label: 'Symbol A-Z' },
]

export default function SymbolList({ symbols, loading, onSelect, positionSymbols = null }) {
  const [filter, setFilter] = useState('')
  const [sort, setSort] = useState('composite_desc')
  const [onlyPositions, setOnlyPositions] = useState(false)

  // / resolve held-symbols set
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
                  {...clickableProps(() => onSelect(s.symbol))}
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
