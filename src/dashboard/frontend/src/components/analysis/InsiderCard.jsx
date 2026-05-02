import { useApi } from '../../hooks/useApi'
import { fmtVal } from './formatters'

// / insider activity card
export default function InsiderCard({ insiderTrades, score, symbol }) {
  const details = (score?.details && typeof score.details === 'object') ? score.details : {}
  const { data: apiTrades } = useApi(`/api/insider/${symbol}`, 60000)
  const trades = (apiTrades && apiTrades.length > 0) ? apiTrades : insiderTrades
  const hasTradeRows = trades && trades.length > 0

  if (!hasTradeRows) {
    const sig = details.insider_signal
    const str = details.insider_score_100
    if (!sig) return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No insider activity</div>
    const tone = sig === 'bullish' ? 'pos' : sig === 'bearish' ? 'neg' : 'dim'
    return (
      <div style={{ padding: '11px 14px', fontFamily: 'var(--mono)', fontSize: 11.5 }}>
        <span className={tone}><b>{sig}</b></span>
        {str != null && <span className="dim"> · strength {parseFloat(str).toFixed(0)}</span>}
      </div>
    )
  }

  const buys = trades.filter(t => t.transaction_type === 'buy')
  const sells = trades.filter(t => t.transaction_type === 'sell')
  const buyTotal = buys.reduce((s, t) => s + parseFloat(t.total_value || 0), 0)
  const sellTotal = sells.reduce((s, t) => s + parseFloat(t.total_value || 0), 0)
  const signedStrength = details.insider_signed_strength
  const insiderState = signedStrength != null
    ? (signedStrength > 0 ? 'bullish' : signedStrength < 0 ? 'bearish' : 'neutral')
    : (buyTotal > sellTotal ? 'bullish' : buyTotal < sellTotal ? 'bearish' : 'neutral')
  const headerTone = insiderState === 'bullish' ? 'pos' : insiderState === 'bearish' ? 'neg' : 'dim'
  const insiderStrength = details.insider_score_100
  const prefix = signedStrength != null && signedStrength > 0 ? '+' : ''

  return (
    <div>
      <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 11.5 }}>
        {insiderStrength != null && (
          <span className={headerTone}><b>{prefix}{parseFloat(insiderStrength).toFixed(1)} strength</b></span>
        )}
        <span className="dim"> · {buys.length} buys ({fmtVal(buyTotal)}) / {sells.length} sells ({fmtVal(sellTotal)})</span>
      </div>
      <div style={{ maxHeight: 280, overflowY: 'auto' }}>
        {trades.slice(0, 30).map((t, i) => {
          const isBuy = t.transaction_type === 'buy'
          const date = t.filing_date?.split('T')[0] || '—'
          const value = fmtVal(parseFloat(t.total_value || 0))
          const shares = parseInt(t.shares || 0).toLocaleString()
          return (
            <div className="feed-row" key={i}>
              <span className="ts">{date}</span>
              <span className="nm" title={t.insider_name}>
                <b>{t.insider_name || '—'}</b>
                {t.insider_title && <span className="dim"> · {t.insider_title}</span>}
              </span>
              <span className={`v ${isBuy ? 'pos' : 'neg'}`}>
                {isBuy ? 'BUY' : 'SELL'} {shares} · {value}
              </span>
              <span></span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
