import Card from '../ui/Card'
import { useApi } from '../../hooks/useApi'

// / recent near-miss events

export default function NearMissesPanel() {
  const { data } = useApi('/api/observation-log?hours=24&limit=30', 60000)
  const rows = Array.isArray(data?.recent) ? data.recent : []

  return (
    <Card title={<><b>recent near-misses</b></>} meta="last 24h" p0>
      {rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">no near-misses 24h</div>
        </div>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>time</th>
              <th>strategy</th>
              <th>symbol</th>
              <th>passed</th>
              <th>reason</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 12).map((r, i) => {
              const ts = r.created_at?.split('T')[1]?.slice(0, 5) || ''
              const frac = r.total_count > 0 ? `${r.passed_count}/${r.total_count}` : '—'
              return (
                <tr key={`${r.strategy_id}-${i}`}>
                  <td className="dim tiny">{ts}</td>
                  <td className="dim tiny">{r.strategy_id}</td>
                  <td className="sym">{r.symbol}</td>
                  <td className="dim tiny">{frac}</td>
                  <td className="dim">{r.failed_reason}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Card>
  )
}
