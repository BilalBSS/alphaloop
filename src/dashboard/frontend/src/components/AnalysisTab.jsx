import { useState, useEffect, useCallback } from 'react'
import { useApi } from '../hooks/useApi'
import Panel from './Panel'
import HeroBanner from './HeroBanner'
import SymbolList, { SynthesisPanel, StrategyEvalPanel } from './analysis/SymbolList'
import SymbolDetail from './analysis/SymbolDetail'

// / relative time — "2h ago" / "34m ago" / "never"
function timeAgo(ts) {
  if (!ts) return 'never'
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 0) return 'just now'
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

// / hero — last analyst cycle + synthesis top buy/avoid.
// / /api/symbols returns `date` on each row — use max as the last analyst pass.
function AnalysisHero({ symbols, synthesis }) {
  let lastTs = null
  let analyzedCount = 0
  if (Array.isArray(symbols)) {
    for (const s of symbols) {
      const t = s.date || s.analyzed_at || s.updated_at || s.created_at
      if (t) {
        analyzedCount++
        const ms = new Date(t).getTime()
        if (Number.isFinite(ms) && (!lastTs || ms > lastTs)) lastTs = ms
      }
    }
  }
  const topBuy = synthesis?.top_buys?.[0]
  const topAvoid = synthesis?.top_avoids?.[0]
  const buySym = typeof topBuy === 'object' ? topBuy?.symbol : topBuy
  const avoidSym = typeof topAvoid === 'object' ? topAvoid?.symbol : topAvoid
  const total = Array.isArray(symbols) ? symbols.length : 0

  return (
    <HeroBanner>
      <div className="hero-metric">
        <span className="hero-metric-label">Last analyst cycle</span>
        <span className="hero-metric-value-sm font-mono">{timeAgo(lastTs ? new Date(lastTs).toISOString() : null)}</span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Symbols scored</span>
        <span className="hero-metric-value font-mono">
          {analyzedCount}<span className="text-text-muted text-sm font-normal"> / {total || '—'}</span>
        </span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Top buy</span>
        <span className="hero-metric-value-sm font-mono pnl-positive">{buySym || '—'}</span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Top avoid</span>
        <span className="hero-metric-value-sm font-mono pnl-negative">{avoidSym || '—'}</span>
      </div>
    </HeroBanner>
  )
}

// / main tab: synthesis + symbol list or detail view
export default function AnalysisTab() {
  const [selectedSymbol, setSelectedSymbol] = useState(null)
  const symbols = useApi('/api/symbols', 60000)
  const { data: synthesis } = useApi('/api/synthesis', 120000)

  // / browser back button support
  const selectSymbol = useCallback((sym) => {
    setSelectedSymbol(sym)
    if (sym) window.history.pushState({ symbol: sym }, '')
  }, [])

  useEffect(() => {
    const onPop = () => setSelectedSymbol(null)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  if (selectedSymbol) {
    return (
      <SymbolDetail
        key={selectedSymbol}
        symbol={selectedSymbol}
        onBack={() => { window.history.back() }}
      />
    )
  }

  return (
    <div className="space-y-6">
      <AnalysisHero symbols={symbols.data} synthesis={synthesis} />

      <Panel title="Daily Synthesis">
        <SynthesisPanel onSelect={selectSymbol} />
      </Panel>
      <Panel title="Strategy Evaluation">
        <StrategyEvalPanel onSelect={selectSymbol} />
      </Panel>
      <Panel title="Symbol Analysis">
        <SymbolList
          symbols={symbols.data}
          loading={symbols.loading}
          onSelect={selectSymbol}
        />
      </Panel>
    </div>
  )
}
