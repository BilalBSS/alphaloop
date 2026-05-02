import Card from '../ui/Card'
import { useApi } from '../../hooks/useApi'

// / strategies with most near-misses

export default function CloseToFiringPanel() {
  const { data } = useApi('/api/observation-log?hours=24&limit=30', 60000)
  const rows = Array.isArray(data?.by_strategy) ? data.by_strategy : []

  return (
    <Card title={<><b>close-to-firing</b> · by strategy</>} meta="N−1 of N entry clauses" p0>
      {rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">no near-misses 24h</div>
        </div>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>strategy</th>
              <th className="r">near-misses</th>
              <th className="r">N−1 technical</th>
              <th className="r">fund gate</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 12).map((r) => (
              <tr key={r.strategy_id}>
                <td className="dim tiny">{r.strategy_id}</td>
                <td className="r">{r.total}</td>
                <td className="r warn">{r.n_minus_1}</td>
                <td className="r dim">{r.fundamental_gate}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}
