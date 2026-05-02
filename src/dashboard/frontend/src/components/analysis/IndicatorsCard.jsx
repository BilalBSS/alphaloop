import { useApi } from '../../hooks/useApi'

// / technical indicators table
export default function IndicatorsCard({ symbol, tf }) {
  const tfParam = tf === '2h' ? '1Hour' : '1Day'
  const { data, loading } = useApi(`/api/indicators/${symbol}?limit=1&timeframe=${tfParam}`, 60000)

  if (loading && !data) return <div className="dim" style={{ padding: 14, fontSize: 12 }}>Loading...</div>

  const latest = Array.isArray(data) && data.length > 0 ? data[0] : null
  if (!latest) return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No {tf} indicator data yet</div>

  const rows = [
    { label: 'RSI (14)', key: 'rsi14', fmt: v => v?.toFixed(1), tone: v => v > 70 ? 'neg' : v < 30 ? 'pos' : '' },
    { label: 'MACD', key: 'macd', fmt: v => v?.toFixed(4), tone: v => v > 0 ? 'pos' : 'neg' },
    { label: 'MACD histogram', key: 'macd_histogram', fmt: v => v?.toFixed(4), tone: v => v > 0 ? 'pos' : 'neg' },
    { label: 'ADX', key: 'adx', fmt: v => v?.toFixed(1), tone: v => v > 25 ? 'pos' : 'dim' },
    { label: 'SMA 20', key: 'sma20', fmt: v => `$${v?.toFixed(2)}` },
    { label: 'BB upper', key: 'bb_upper', fmt: v => `$${v?.toFixed(2)}`, tone: () => 'dim' },
    { label: 'BB lower', key: 'bb_lower', fmt: v => `$${v?.toFixed(2)}`, tone: () => 'dim' },
    { label: 'ATR (14)', key: 'atr', fmt: v => v?.toFixed(4) },
  ]

  return (
    <table className="tbl">
      <tbody>
        {rows.map(r => {
          const v = latest ? parseFloat(latest[r.key]) : NaN
          const display = isNaN(v) ? '—' : r.fmt(v)
          const cls = r.tone && !isNaN(v) ? r.tone(v) : ''
          return (
            <tr key={r.label}>
              <td>{r.label}</td>
              <td className={`r ${cls}`}>{display}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
