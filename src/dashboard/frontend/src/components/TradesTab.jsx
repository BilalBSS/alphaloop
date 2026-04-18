import { useState } from 'react'
import Panel from './Panel'
import { SkeletonTable } from './Skeleton'
import { useApi } from '../hooks/useApi'

// / format a long text blob for excerpt display (first 300 chars)
function excerpt(text, len = 300) {
  if (!text || typeof text !== 'string') return ''
  return text.length > len ? text.slice(0, len) + '…' : text
}

// / reasoning drilldown: indicators fired + dual-LLM excerpts + wiki refs
function TradeDetail({ tradeId }) {
  const { data, loading, error } = useApi(`/api/trades/${tradeId}/detail`, null)

  if (loading) return <div className="text-text-muted text-sm p-3">Loading reasoning...</div>
  if (error) return <div className="text-loss text-sm p-3">Failed to load detail: {error}</div>
  if (!data) return <div className="text-text-muted text-sm p-3">No detail available</div>

  const indicators = data.indicators_fired || data.signal_details?.indicators_fired || []
  const signalDetails = data.signal_details || {}
  // / llm excerpts — may come from signal metadata or analysis_scores.details join
  const llmGroq = data.llm_groq || signalDetails.llm_analysis_groq || data.analysis?.llm_analysis_groq
  const llmDeepseek = data.llm_deepseek || signalDetails.llm_analysis_deepseek || data.analysis?.llm_analysis_deepseek
  const consensus = data.ai_consensus || signalDetails.ai_consensus || data.analysis?.ai_consensus
  const wikiRefs = data.wiki_refs || data.wiki_references || signalDetails.wiki_refs || []
  const strategyId = data.strategy_id || signalDetails.strategy_id
  const strength = data.strength ?? signalDetails.strength

  const consensusColor = {
    bullish: 'text-profit', bearish: 'text-loss', neutral: 'text-warning', disagree: 'text-accent',
  }[consensus] || 'text-text-muted'

  return (
    <div className="bg-bg-primary border-l-2 border-accent px-3 py-2 space-y-2 text-xs">
      {/* strategy + strength */}
      <div className="flex flex-wrap gap-3 text-[11px]">
        {strategyId && (
          <div>
            <span className="text-text-secondary uppercase">Strategy:</span>
            <span className="ml-1 font-mono text-text-primary">{strategyId}</span>
          </div>
        )}
        {strength != null && (
          <div>
            <span className="text-text-secondary uppercase">Signal Strength:</span>
            <span className="ml-1 font-mono text-text-primary">{parseFloat(strength).toFixed(2)}</span>
          </div>
        )}
        {consensus && (
          <div>
            <span className="text-text-secondary uppercase">AI Consensus:</span>
            <span className={`ml-1 font-semibold uppercase ${consensusColor}`}>{consensus}</span>
          </div>
        )}
      </div>

      {/* indicators */}
      {indicators.length > 0 && (
        <div>
          <div className="text-[10px] uppercase text-text-secondary mb-1">Indicators Fired</div>
          <div className="flex flex-wrap gap-1">
            {indicators.map((ind, i) => {
              // / each entry may be a string or { name, value, condition }
              const label = typeof ind === 'string' ? ind : (ind.name || ind.indicator || '--')
              const value = typeof ind === 'object' ? ind.value : null
              const condition = typeof ind === 'object' ? ind.condition : null
              return (
                <span
                  key={i}
                  className="px-2 py-0.5 bg-bg-surface border border-border text-[10px] font-mono"
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

      {/* dual-llm excerpts */}
      {(llmGroq || llmDeepseek) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {llmGroq && (
            <div className="bg-bg-surface border border-border p-2">
              <div className="text-[10px] uppercase text-text-secondary mb-1">Groq</div>
              <div className="text-[11px] text-text-primary whitespace-pre-wrap leading-relaxed">
                {excerpt(llmGroq)}
              </div>
            </div>
          )}
          {llmDeepseek && (
            <div className="bg-bg-surface border border-border p-2">
              <div className="text-[10px] uppercase text-text-secondary mb-1">DeepSeek</div>
              <div className="text-[11px] text-text-primary whitespace-pre-wrap leading-relaxed">
                {excerpt(llmDeepseek)}
              </div>
            </div>
          )}
        </div>
      )}

      {/* wiki references */}
      {wikiRefs.length > 0 && (
        <div>
          <div className="text-[10px] uppercase text-text-secondary mb-1">Wiki References</div>
          <ul className="space-y-0.5">
            {wikiRefs.map((ref, i) => {
              const path = typeof ref === 'string' ? ref : (ref.path || ref.doc || '--')
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
        <td className={`px-2 py-1 ${trade.side === 'buy' ? 'text-profit' : 'text-loss'}`}>
          {trade.side?.toUpperCase()}
        </td>
        <td className="px-2 py-1 text-right font-mono">{qtyDisplay}</td>
        <td className="px-2 py-1 text-right font-mono">
          ${parseFloat(trade.price || 0).toFixed(2)}
        </td>
        <td className={`px-2 py-1 text-right font-mono ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
          {pnl !== 0 ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}` : '--'}
        </td>
        <td className="px-2 py-1 text-text-secondary truncate max-w-[100px]">{trade.strategy_id || '--'}</td>
        <td className="px-2 py-1 text-right text-text-muted whitespace-nowrap">
          {trade.created_at?.replace('T', ' ').slice(0, 16) || '--'}
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

export default function TradesTab({ trades, loading }) {
  const [expandedId, setExpandedId] = useState(null)

  if (loading) {
    return <Panel title="Trade Log"><SkeletonTable rows={8} cols={7} /></Panel>
  }

  return (
    <Panel title="Trade Log">
      {!trades || trades.length === 0 ? (
        <div className="text-text-muted text-sm py-8">No trades recorded yet</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-secondary text-[11px] uppercase">
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
  )
}
