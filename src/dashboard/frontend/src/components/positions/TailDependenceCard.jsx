import Card from '../ui/Card'
import { useApiLive } from '../../hooks/useApiLive'

// / λ_lower aggregated + per-position

export default function TailDependenceCard() {
  const { data, loading, error } = useApiLive('/api/portfolio/tail-dependence', 60000, ['position_update'])

  const lamRaw = data?.lambda_lower ?? data?.aggregated_lambda
  const lam = lamRaw != null ? Number(lamRaw) : null
  const threshold = Number(data?.threshold ?? 0.3)
  const perPos = Array.isArray(data?.per_position) ? data.per_position : []

  const tone = lam == null ? '' : lam < 0.2 ? 'pos' : lam < threshold ? 'warn' : 'neg'
  const label = lam == null ? '—' : lam < 0.2 ? 'low' : lam < threshold ? 'elevated' : 'high'
  const note = lam == null
    ? ''
    : lam < 0.2
      ? 'portfolio well-diversified against crash co-movement'
      : lam < threshold
        ? 'some crash correlation — monitor new entries'
        : 'high crash co-movement — new entries may be rejected'

  return (
    <Card title={<><b>tail dependence</b> · λ lower</>} meta={<><code>/api/portfolio/tail-dependence</code></>}>
      {loading && !data ? (
        <div className="empty-state"><div className="empty-state-title">loading λ</div></div>
      ) : error ? (
        <div className="empty-state"><div className="empty-state-title">failed to load</div><div className="empty-state-hint">{error}</div></div>
      ) : lam == null ? (
        <div className="empty-state">
          <div className="empty-state-title">no tail dependence yet</div>
          <div className="empty-state-hint">copula fit needs ≥ 2 positions.</div>
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 6 }}>
            <span className={`big ${tone}`} style={{ fontSize: 32, fontFamily: 'var(--mono)', fontWeight: 600, lineHeight: 1 }}>
              {lam.toFixed(3)}
            </span>
            <span className={tone} style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.14em', fontWeight: 600 }}>
              {label}
            </span>
            <span className="dim" style={{ fontSize: 10.5 }}>
              threshold {threshold.toFixed(2)}
            </span>
          </div>
          <div className="dim" style={{ fontSize: 11, marginBottom: 10 }}>{note}</div>
          {perPos.length > 0 && (
            <table className="tbl">
              <thead>
                <tr>
                  <th>symbol</th>
                  <th className="r">λ</th>
                </tr>
              </thead>
              <tbody>
                {perPos.slice(0, 8).map((p) => {
                  const v = Number(p.lambda) || 0
                  const t = v < 0.2 ? 'pos' : v < threshold ? 'warn' : 'neg'
                  return (
                    <tr key={p.symbol}>
                      <td className="sym">{p.symbol}</td>
                      <td className={`r ${t}`}>{v.toFixed(3)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </>
      )}
    </Card>
  )
}
