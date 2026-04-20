import { useState, useMemo } from 'react'
import Panel from './Panel'
import { SkeletonTable } from './Skeleton'
import EmptyState from './EmptyState'
import HeroBanner from './HeroBanner'
import { useApi } from '../hooks/useApi'

// / format a long text blob for excerpt display (first 300 chars)
function excerpt(text, len = 300) {
  if (!text || typeof text !== 'string') return ''
  return text.length > len ? text.slice(0, len) + '…' : text
}

// / relative time helper — "2h ago" / "3d ago"
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

// / reasoning drilldown: indicators fired + dual-LLM excerpts + wiki refs
function TradeDetail({ tradeId }) {
  const { data, loading, error } = useApi(`/api/trades/${tradeId}/detail`, null)

  if (loading) return <div className="text-text-muted text-sm p-3">Loading reasoning...</div>
  if (error) return <div className="pnl-negative text-sm p-3">Failed to load detail: {error}</div>
  if (!data) return <div className="text-text-muted text-sm p-3">No detail available</div>

  const indicators = data.indicators_fired || data.signal_details?.indicators_fired || []
  const signalDetails = data.signal_details || {}
  const llmGroq = data.llm_groq || signalDetails.llm_analysis_groq || data.analysis?.llm_analysis_groq
  const llmDeepseek = data.llm_deepseek || signalDetails.llm_analysis_deepseek || data.analysis?.llm_analysis_deepseek
  const consensus = data.ai_consensus || signalDetails.ai_consensus || data.analysis?.ai_consensus
  const wikiRefs = data.wiki_refs || data.wiki_references || signalDetails.wiki_refs || []
  const strategyId = data.strategy_id || signalDetails.strategy_id
  const strength = data.strength ?? signalDetails.strength

  const consensusColor = {
    bullish: 'pnl-positive', bearish: 'pnl-negative', neutral: 'text-warning', disagree: 'text-accent',
  }[consensus] || 'text-text-muted'

  return (
    <div className="bg-bg-primary border-l-2 border-accent px-3 py-3 space-y-3 text-xs">
      <div className="flex flex-wrap gap-4 text-[11px]">
        {strategyId && (
          <div>
            <span className="type-metric-label">Strategy</span>
            <span className="ml-1 font-mono text-text-primary">{strategyId}</span>
          </div>
        )}
        {strength != null && (
          <div>
            <span className="type-metric-label">Signal Strength</span>
            <span className="ml-1 font-mono text-text-primary">{parseFloat(strength).toFixed(2)}</span>
          </div>
        )}
        {consensus && (
          <div>
            <span className="type-metric-label">AI Consensus</span>
            <span className={`ml-1 font-semibold uppercase ${consensusColor}`}>{consensus}</span>
          </div>
        )}
      </div>

      {indicators.length > 0 && (
        <div>
          <div className="type-metric-label mb-1">Indicators Fired</div>
          <div className="flex flex-wrap gap-1">
            {indicators.map((ind, i) => {
              const label = typeof ind === 'string' ? ind : (ind.name || ind.indicator || '—')
              const value = typeof ind === 'object' ? ind.value : null
              const condition = typeof ind === 'object' ? ind.condition : null
              return (
                <span
                  key={i}
                  className="px-2 py-0.5 bg-bg-surface border border-border text-[10px] font-mono rounded"
                  title={condition || ''}
                >
                  {label}
                  {value != null && <span className="text-text-muted ml-1">= {typeof value === 'number' ? value.toFixed(2) : value}</span>}
                </span>
              )
            })}
          </div>
        </div>
      )}

      {(llmGroq || llmDeepseek) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {llmGroq && (
            <div className="bg-bg-surface border border-border rounded p-2">
              <div className="type-metric-label mb-1">Groq</div>
              <div className="text-[11px] text-text-primary whitespace-pre-wrap leading-relaxed">
                {excerpt(llmGroq)}
              </div>
            </div>
          )}
          {llmDeepseek && (
            <div className="bg-bg-surface border border-border rounded p-2">
              <div className="type-metric-label mb-1">DeepSeek</div>
              <div className="text-[11px] text-text-primary whitespace-pre-wrap leading-relaxed">
                {excerpt(llmDeepseek)}
              </div>
            </div>
          )}
        </div>
      )}

      {wikiRefs.length > 0 && (
        <div>
          <div className="type-metric-label mb-1">Wiki References</div>
          <ul className="space-y-0.5">
            {wikiRefs.map((ref, i) => {
              const path = typeof ref === 'string' ? ref : (ref.path || ref.doc || '—')
              const title = typeof ref === 'object' ? ref.title : null
              return (
                <li key={i} className="text-[11px] font-mono text-accent">
                  {path}
                  {title && <span className="ml-2 text-text-muted">— {title}</span>}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {indicators.length === 0 && !llmGroq && !llmDeepseek && wikiRefs.length === 0 && (
        <div className="text-text-muted text-sm">No reasoning metadata persisted for this trade.</div>
      )}
    </div>
  )
}

function TradeRow({ trade, expanded, onToggle }) {
  const pnl = parseFloat(trade.pnl || 0)
  const qty = parseFloat(trade.qty || 0)
  const qtyDisplay = qty < 1 ? qty.toPrecision(4) : qty % 1 === 0 ? qty.toFixed(0) : qty.toFixed(2)
  const tradeId = trade.id || trade.trade_id
  return (
    <>
      <tr
        onClick={onToggle}
        className="hover:bg-bg-hover border-t border-border cursor-pointer"
        style={{ height: 36 }}
      >
        <td className="px-2 py-1 sticky left-0 bg-bg-surface">
          <span className="text-text-muted text-[10px] inline-block w-3">{expanded ? '▾' : '▸'}</span>
          <span className="font-mono font-semibold ml-1">{trade.symbol}</span>
        </td>
        <td className={`px-2 py-1 ${trade.side === 'buy' ? 'pnl-positive' : 'pnl-negative'}`}>
          {trade.side?.toUpperCase()}
        </td>
        <td className="px-2 py-1 text-right font-mono">{qtyDisplay}</td>
        <td className="px-2 py-1 text-right font-mono">
          ${parseFloat(trade.price || 0).toFixed(2)}
        </td>
        <td className={`px-2 py-1 text-right font-mono ${pnl > 0 ? 'pnl-positive' : pnl < 0 ? 'pnl-negative' : 'text-text-muted'}`}>
          {pnl !== 0 ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}` : '—'}
        </td>
        <td className="px-2 py-1 text-text-secondary truncate max-w-[100px]">{trade.strategy_id || '—'}</td>
        <td className="px-2 py-1 text-right text-text-muted whitespace-nowrap">
          {trade.created_at?.replace('T', ' ').slice(0, 16) || '—'}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={7} className="p-0">
            {tradeId ? (
              <TradeDetail tradeId={tradeId} />
            ) : (
              <div className="bg-bg-primary border-l-2 border-text-muted px-3 py-2 text-text-muted text-xs">
                Trade ID missing — cannot fetch reasoning
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

function TradesHero({ trades }) {
  // / open-position count belongs on portfolio tab (authoritative via alpaca).
  // / the naive buys − sells formula mixed buys/sells across unrelated symbols
  // / and contradicted positions_count=0 — dropped.
  const stats = useMemo(() => {
    if (!Array.isArray(trades) || trades.length === 0) {
      return { total: 0, buys: 0, sells: 0, lastTs: null }
    }
    let buys = 0
    let sells = 0
    let lastTs = null
    for (const t of trades) {
      if ((t.side || '').toLowerCase() === 'buy') buys++
      if ((t.side || '').toLowerCase() === 'sell') sells++
      const ts = t.created_at ? new Date(t.created_at).getTime() : 0
      if (ts && (!lastTs || ts > lastTs)) lastTs = ts
    }
    return { total: trades.length, buys, sells, lastTs }
  }, [trades])

  return (
    <HeroBanner>
      <div className="hero-metric">
        <span className="hero-metric-label">Total trades</span>
        <span className="hero-metric-value font-mono">{stats.total}</span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Buys</span>
        <span className="hero-metric-value font-mono pnl-positive">{stats.buys}</span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Sells</span>
        <span className="hero-metric-value font-mono pnl-negative">{stats.sells}</span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Last trade</span>
        <span className="hero-metric-value-sm font-mono">
          {timeAgo(stats.lastTs ? new Date(stats.lastTs).toISOString() : null)}
        </span>
      </div>
    </HeroBanner>
  )
}

export default function TradesTab({ trades, loading }) {
  const [expandedId, setExpandedId] = useState(null)

  if (loading) {
    return (
      <div className="space-y-6">
        <TradesHero trades={[]} />
        <Panel title="Trade Log"><SkeletonTable rows={8} cols={7} /></Panel>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <TradesHero trades={trades} />
      <Panel title="Trade Log">
        {!trades || trades.length === 0 ? (
          <EmptyState
            title="No trades recorded yet"
            hint="Trade log populates on first executor fill. The strategy agent evaluates every 15 minutes during market hours."
          />
        ) : (
          <div className="overflow-x-auto table-scroll-fade">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-secondary text-[11px] uppercase sticky top-0 bg-bg-surface">
                  <th className="text-left px-2 py-1 sticky left-0 bg-bg-surface">Symbol</th>
                  <th className="text-left px-2 py-1">Side</th>
                  <th className="text-right px-2 py-1">Qty</th>
                  <th className="text-right px-2 py-1">Price</th>
                  <th className="text-right px-2 py-1">P&L</th>
                  <th className="text-left px-2 py-1">Strategy</th>
                  <th className="text-right px-2 py-1">Date</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => {
                  const key = t.id ?? t.trade_id ?? `${t.symbol}-${t.created_at}-${i}`
                  return (
                    <TradeRow
                      key={key}
                      trade={t}
                      expanded={expandedId === key}
                      onToggle={() => setExpandedId(expandedId === key ? null : key)}
                    />
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}
