import { useState, Fragment } from 'react'
import Card from '../ui/Card'
import { useApi } from '../../hooks/useApi'

// / fills + row expand

function fmtTs(ts) {
  if (!ts) return '—'
  return ts.replace('T', ' ').slice(0, 16)
}

function fmtUsd(n) {
  if (n == null || !Number.isFinite(Number(n))) return '—'
  return `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function fmtQty(q) {
  const n = Number(q)
  if (!Number.isFinite(n)) return '—'
  if (n < 1) return n.toPrecision(4)
  return n % 1 === 0 ? n.toFixed(0) : n.toFixed(2)
}

function excerpt(t, len = 280) {
  if (!t || typeof t !== 'string') return ''
  return t.length > len ? t.slice(0, len) + '…' : t
}

function TradeDetail({ tradeId }) {
  const { data, loading, error } = useApi(`/api/trades/${tradeId}/detail`, null)

  if (loading) return <div className="dim" style={{ padding: '10px 14px', fontSize: 11 }}>loading reasoning…</div>
  if (error) return <div className="neg" style={{ padding: '10px 14px', fontSize: 11 }}>{error}</div>
  if (!data) return <div className="dim" style={{ padding: '10px 14px', fontSize: 11 }}>no detail available</div>

  const indicators = data.indicators_fired || data.signal_details?.indicators_fired || []
  const sd = data.signal_details || {}
  const llmGroq = data.llm_groq || sd.llm_analysis_groq || data.analysis?.llm_analysis_groq
  const llmDs = data.llm_deepseek || sd.llm_analysis_deepseek || data.analysis?.llm_analysis_deepseek
  const consensus = data.ai_consensus || sd.ai_consensus
  const wikiRefs = data.wiki_refs || data.wiki_references || sd.wiki_refs || []
  const consensusVariant = { bullish: 'bull', bearish: 'bear', neutral: 'sideways' }[consensus] || ''

  return (
    <div style={{ padding: '12px 14px', borderLeft: '2px solid var(--acc)', background: 'var(--bg-2)', fontSize: 11 }}>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 10 }}>
        {sd.strategy_id && <span><span className="dim">strategy </span><span className="sym">{sd.strategy_id}</span></span>}
        {sd.strength != null && <span><span className="dim">strength </span>{Number(sd.strength).toFixed(2)}</span>}
        {consensus && <span><span className="dim">consensus </span><span className={consensusVariant}>{consensus}</span></span>}
      </div>
      {indicators.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div className="dim" style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 4 }}>indicators</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {indicators.map((ind, i) => {
              const label = typeof ind === 'string' ? ind : (ind.name || ind.indicator || '—')
              const value = typeof ind === 'object' ? ind.value : null
              return (
                <span key={i} style={{ padding: '1px 6px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 10 }}>
                  {label}{value != null && <span className="dim"> {typeof value === 'number' ? value.toFixed(2) : value}</span>}
                </span>
              )
            })}
          </div>
        </div>
      )}
      {(llmGroq || llmDs) && (
        <div className="grid c2" style={{ marginBottom: 10 }}>
          {llmGroq && (
            <div className="card">
              <div className="card-h"><span className="t">groq</span></div>
              <div className="card-b" style={{ fontSize: 11, lineHeight: 1.5 }}>{excerpt(llmGroq)}</div>
            </div>
          )}
          {llmDs && (
            <div className="card">
              <div className="card-h"><span className="t">deepseek</span></div>
              <div className="card-b" style={{ fontSize: 11, lineHeight: 1.5 }}>{excerpt(llmDs)}</div>
            </div>
          )}
        </div>
      )}
      {wikiRefs.length > 0 && (
        <div>
          <div className="dim" style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 4 }}>wiki refs</div>
          <ul style={{ margin: 0, paddingLeft: 16 }}>
            {wikiRefs.map((ref, i) => {
              const path = typeof ref === 'string' ? ref : (ref.path || ref.doc || '—')
              return <li key={i} style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--acc)' }}>{path}</li>
            })}
          </ul>
        </div>
      )}
      {indicators.length === 0 && !llmGroq && !llmDs && wikiRefs.length === 0 && (
        <div className="dim">no reasoning metadata persisted</div>
      )}
    </div>
  )
}

export default function TradeLedger({ trades }) {
  const [expandedId, setExpandedId] = useState(null)

  if (!trades || trades.length === 0) {
    return (
      <Card p0>
        <div className="empty-state">
          <div className="empty-state-title">no trades yet</div>
          <div className="empty-state-hint">strategy agent evaluates every 15m during market hours.</div>
        </div>
      </Card>
    )
  }

  return (
    <Card p0>
      <table className="tbl">
        <thead>
          <tr>
            <th>timestamp</th>
            <th>symbol</th>
            <th>side</th>
            <th className="r">qty</th>
            <th className="r">price</th>
            <th className="r">notional</th>
            <th>strategy</th>
            <th className="r">realized</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => {
            const key = t.id ?? t.trade_id ?? `${t.symbol}-${t.created_at}-${i}`
            const open = expandedId === key
            const pnl = parseFloat(t.pnl || 0)
            const price = parseFloat(t.price || 0)
            const qty = parseFloat(t.qty || 0)
            const notional = price * Math.abs(qty)
            const sideTone = t.side === 'buy' ? 'pos' : t.side === 'sell' ? 'neg' : ''
            const pnlTone = pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : 'dim'
            return (
              <Fragment key={key}>
                <tr className="click" onClick={() => setExpandedId(open ? null : key)}>
                  <td className="dim tiny">{fmtTs(t.created_at)}</td>
                  <td className="sym">{t.symbol}</td>
                  <td className={sideTone}>{(t.side || '').toUpperCase()}</td>
                  <td className="r">{fmtQty(qty)}</td>
                  <td className="r">{fmtUsd(price)}</td>
                  <td className="r">{fmtUsd(notional)}</td>
                  <td className="dim tiny">{t.strategy_id || '—'}</td>
                  <td className={`r ${pnlTone}`}>{pnl !== 0 ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}` : '—'}</td>
                </tr>
                {open && (
                  <tr>
                    <td colSpan={8} style={{ padding: 0 }}>
                      {t.id ?? t.trade_id ? (
                        <TradeDetail tradeId={t.id ?? t.trade_id} />
                      ) : (
                        <div className="dim" style={{ padding: '10px 14px', fontSize: 11 }}>trade id missing</div>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </Card>
  )
}
