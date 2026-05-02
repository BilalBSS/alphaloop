import { useState } from 'react'
import Card from '../ui/Card'

// / handbuilt vs alpha158

export default function FeatureBenchmarkPanel() {
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)

  const run = async (sym = 'SPY') => {
    setLoading(true)
    setErr(null)
    try {
      const resp = await fetch(`/api/feature-benchmark?symbol=${encodeURIComponent(sym)}`)
      if (!resp.ok) throw new Error(`${resp.status}`)
      setResult(await resp.json())
    } catch (e) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }

  const winner = result?.winner

  return (
    <Card
      title={<><b>feature benchmark</b> · handbuilt vs alpha158</>}
      meta={result?.symbol ? `symbol ${result.symbol}` : 'cached 1h server-side'}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span className="dim" style={{ fontSize: 11 }}>
          {result ? '5y daily bars · LightGBM' : 'click to run a fast A/B'}
        </span>
        <div style={{ display: 'flex', gap: 6 }}>
          {['SPY', 'AAPL'].map((sym) => (
            <button
              key={sym}
              onClick={() => run(sym)}
              disabled={loading}
              style={{
                fontSize: 10, padding: '2px 8px',
                background: 'transparent', border: '1px solid var(--line)',
                color: 'var(--ink-2)', cursor: 'pointer',
                opacity: loading ? 0.4 : 1,
              }}
            >
              {loading ? '…' : `run ${sym}`}
            </button>
          ))}
        </div>
      </div>
      {err && <div className="neg" style={{ fontSize: 11, marginBottom: 8 }}>{err}</div>}
      {result?.error && <div className="dim" style={{ fontSize: 11 }}>benchmark unavailable — {result.error}</div>}
      {result && !result.error && (
        <table className="tbl">
          <thead>
            <tr>
              <th>feature set</th>
              <th className="r">brier ↓</th>
              <th className="r">IC ↑</th>
              <th className="r">features</th>
            </tr>
          </thead>
          <tbody>
            {['handbuilt', 'alpha158'].map((label) => {
              const r = result[label]
              if (!r) return null
              if (r.error) {
                return (
                  <tr key={label}>
                    <td className="sym">{label}</td>
                    <td colSpan={3} className="dim">{r.error}</td>
                  </tr>
                )
              }
              const isWinner = winner === label
              return (
                <tr key={label} className={isWinner ? 'sel' : ''}>
                  <td className={isWinner ? 'pos sym' : 'sym'}>{label}{isWinner ? ' ★' : ''}</td>
                  <td className="r">{r.brier}</td>
                  <td className="r">{r.ic ?? '—'}</td>
                  <td className="r dim">{r.feature_count}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Card>
  )
}
