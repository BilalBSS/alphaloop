import { useApi } from '../../hooks/useApi'

// / per-strategy metrics
export default function QuantMetricsCard({ symbol }) {
  const { data, loading } = useApi(`/api/quant-metrics/${symbol}`, 60000)

  if (loading && !data) return <div className="dim" style={{ padding: 14, fontSize: 12 }}>Loading...</div>
  if (!data || data.length === 0) {
    return (
      <div className="dim" style={{ padding: 14, fontSize: 12 }}>
        No quant metrics yet — populated once a strategy closes at least 3 trades on this symbol.
      </div>
    )
  }

  const scoreTone = v => v >= 65 ? 'pos' : v >= 50 ? 'warn' : 'neg'
  const sharpeTone = v => v >= 1 ? 'pos' : v < 0 ? 'neg' : ''
  const ddTone = v => v > -0.10 ? 'pos' : v > -0.20 ? 'warn' : 'neg'
  const winTone = v => v >= 0.5 ? 'pos' : 'neg'
  const brierTone = v => v < 0.20 ? 'pos' : 'warn'

  return (
    <table className="tbl">
      <thead>
        <tr>
          <th>strategy</th>
          <th className="r">sharpe</th>
          <th className="r">sortino</th>
          <th className="r">max dd</th>
          <th className="r">win</th>
          <th className="r">brier</th>
          <th className="r">score</th>
        </tr>
      </thead>
      <tbody>
        {data.map((s, i) => {
          const sharpe = parseFloat(s.sharpe_ratio || 0)
          const sortino = parseFloat(s.sortino_ratio || 0)
          const dd = parseFloat(s.max_drawdown || 0)
          const wr = parseFloat(s.win_rate || 0)
          const brier = parseFloat(s.brier_score || 0)
          const score = parseFloat(s.composite_score || 0)
          return (
            <tr key={i}>
              <td className="dim tiny" title={s.strategy_id}>{s.strategy_id}</td>
              <td className={`r ${sharpeTone(sharpe)}`}>{sharpe.toFixed(2)}</td>
              <td className={`r ${sharpeTone(sortino)}`}>{sortino.toFixed(2)}</td>
              <td className={`r ${ddTone(dd)}`}>{(dd * 100).toFixed(1)}%</td>
              <td className={`r ${winTone(wr)}`}>{(wr * 100).toFixed(0)}%</td>
              <td className={`r ${brierTone(brier)}`}>{brier.toFixed(3)}</td>
              <td className={`r ${scoreTone(score)}`}><b>{score.toFixed(1)}</b></td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
