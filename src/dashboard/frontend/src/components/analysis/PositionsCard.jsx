import { useApi } from '../../hooks/useApi'

// / strategies holding symbol
export default function PositionsCard({ symbol }) {
  const { data, loading } = useApi(`/api/strategy-positions?symbol=${symbol}`, 30000)

  if (loading && !data) return <div className="dim" style={{ padding: 14, fontSize: 12 }}>Loading...</div>
  if (!data || data.length === 0) {
    return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No strategy is currently holding this symbol.</div>
  }

  return (
    <table className="tbl">
      <thead>
        <tr>
          <th>strategy</th>
          <th className="r">qty</th>
          <th className="r">avg entry</th>
          <th className="r">updated</th>
        </tr>
      </thead>
      <tbody>
        {data.map((p, i) => (
          <tr key={i}>
            <td className="dim tiny" title={p.strategy_id}>{p.strategy_id}</td>
            <td className="r">{parseFloat(p.qty).toFixed(0)}</td>
            <td className="r">${parseFloat(p.avg_entry_price || 0).toFixed(2)}</td>
            <td className="r dim">{p.updated_at?.split('T')[0] || '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
