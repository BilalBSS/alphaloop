import Card from '../ui/Card'
import { useApiLive } from '../../hooks/useApiLive'

// / spearman 30d returns matrix

function cellBg(v) {
  const n = typeof v === 'number' ? v : parseFloat(v)
  if (!Number.isFinite(n)) return 'rgba(100,100,120,0.12)'
  const abs = Math.min(1, Math.abs(n))
  if (abs < 0.3) return `rgba(127, 184, 122, ${0.12 + abs * 0.25})`
  if (abs < 0.6) return `rgba(199, 154, 58, ${0.20 + (abs - 0.3) * 0.55})`
  return `rgba(213, 106, 91, ${0.30 + (abs - 0.6) * 1.0})`
}

export default function CorrelationHeatmap() {
  const { data, loading, error } = useApiLive('/api/portfolio/correlation', 60000, ['position_update'])

  const symbols = data?.symbols ?? []
  const matrix = data?.matrix ?? []

  return (
    <Card title={<><b>correlation</b> · 30d returns</>} meta="spearman">
      {loading && !data ? (
        <div className="empty-state"><div className="empty-state-title">loading correlation</div></div>
      ) : error ? (
        <div className="empty-state"><div className="empty-state-title">failed to load</div><div className="empty-state-hint">{error}</div></div>
      ) : symbols.length < 2 ? (
        <div className="empty-state">
          <div className="empty-state-title">need ≥ 2 positions</div>
          <div className="empty-state-hint">correlation needs pairwise return windows.</div>
        </div>
      ) : (
        <>
          <table className="corr">
            <thead>
              <tr>
                <th></th>
                {symbols.map((s) => <th key={s}>{s.length > 6 ? s.slice(0, 6) : s}</th>)}
              </tr>
            </thead>
            <tbody>
              {symbols.map((row, i) => (
                <tr key={row}>
                  <th>{row.length > 6 ? row.slice(0, 6) : row}</th>
                  {symbols.map((col, j) => {
                    const v = matrix[i]?.[j]
                    const n = typeof v === 'number' ? v : parseFloat(v)
                    return (
                      <td
                        key={`${row}-${col}`}
                        style={{ background: cellBg(v) }}
                        title={`${row} ↔ ${col}: ${Number.isFinite(n) ? n.toFixed(3) : '—'}`}
                      >
                        {Number.isFinite(n) ? n.toFixed(2) : '—'}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ fontSize: 10.5, color: 'var(--ink-3)', margin: '10px 0 0', fontFamily: 'var(--mono)' }}>
            <span className="acc">▮</span> high &gt; 0.6 &nbsp; <span className="dim">▮</span> low &lt; 0.3
          </p>
        </>
      )}
    </Card>
  )
}
