import Card from '../ui/Card'
import { useApi } from '../../hooks/useApi'

// / funnel + rejections

export default function SignalFunnelCard({ hours = 24 }) {
  const { data, loading } = useApi(`/api/signal-funnel?hours=${hours}`, 60000)

  const byStatus = data?.by_status || {}
  const totalSignals = Object.values(byStatus).reduce((s, n) => s + (Number(n) || 0), 0)
  const approved = Number(data?.approved_trades ?? byStatus.approved ?? 0)
  const filled = Number(data?.filled_trades ?? 0)
  const reasons = Array.isArray(data?.by_rejection_reason) ? data.by_rejection_reason : []
  const conv = totalSignals > 0 ? ((filled / totalSignals) * 100).toFixed(1) : '—'

  return (
    <Card
      title={<><b>signal funnel</b> · {hours}h</>}
      meta={<><code>/api/signal-funnel</code></>}
    >
      {loading && !data ? (
        <div className="empty-state"><div className="empty-state-title">loading funnel</div></div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 14, alignItems: 'baseline', fontFamily: 'var(--mono)', fontSize: 13, flexWrap: 'wrap' }}>
            <span><b style={{ fontSize: 18 }}>{totalSignals}</b> signals</span>
            <span className="dim">→</span>
            <span className="pos"><b style={{ fontSize: 18 }}>{approved}</b> approved</span>
            <span className="dim">→</span>
            <span className="pos"><b style={{ fontSize: 18 }}>{filled}</b> filled</span>
          </div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 6 }}>
            conversion <span className="dim">{totalSignals} → {filled} = {conv}{conv !== '—' ? '%' : ''}</span> · last {hours}h
          </div>
          <div style={{ marginTop: 14, fontSize: 10, color: 'var(--neg)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 6 }}>
            rejection reasons ({hours}h)
          </div>
          {reasons.length === 0 ? (
            <div className="empty-state" style={{ padding: '12px 0' }}>
              <div className="empty-state-title">no rejections</div>
            </div>
          ) : (
            <table className="tbl">
              <tbody>
                {reasons.map((r) => (
                  <tr key={r.reason}>
                    <td>{r.reason}</td>
                    <td className="r dim">{r.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </Card>
  )
}
