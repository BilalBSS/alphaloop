import { useState } from 'react'
import { useApi } from '../../hooks/useApi'

const MARKETS = ['all', 'equity', 'crypto']

export default function RegimeShiftList() {
  const [market, setMarket] = useState('all')
  const url = market === 'all'
    ? '/api/regime-shifts?limit=100'
    : `/api/regime-shifts?market=${market}&limit=100`
  const { data, loading } = useApi(url, 60000)

  return (
    <>
      <div className="flex gap-1 mb-3 text-xs">
        {MARKETS.map((m) => (
          <button
            key={m}
            onClick={() => setMarket(m)}
            className={`px-2 py-1 rounded border
              ${market === m
                ? 'bg-accent text-bg-primary border-accent'
                : 'bg-bg-primary text-text-secondary border-border hover:text-text-primary'
              }`}
          >
            {m}
          </button>
        ))}
      </div>

      {loading && <div className="text-text-muted text-sm py-8">loading…</div>}
      {!loading && (!data || data.length === 0) && (
        <div className="text-text-muted text-sm py-8">No regime shifts recorded yet.</div>
      )}
      {!loading && data && data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-secondary text-[11px] uppercase">
                <th className="text-left px-2 py-1">Detected</th>
                <th className="text-left px-2 py-1">Market</th>
                <th className="text-left px-2 py-1">Transition</th>
                <th className="text-right px-2 py-1">Confidence</th>
                <th className="text-left px-2 py-1">Wiki</th>
              </tr>
            </thead>
            <tbody>
              {data.map((rs) => (
                <tr key={rs.id} className="border-b border-border/50 hover:bg-bg-primary/50">
                  <td className="px-2 py-1.5 text-text-muted">
                    {rs.detected_at ? new Date(rs.detected_at).toLocaleString() : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-text-secondary">{rs.market}</td>
                  <td className="px-2 py-1.5">
                    <span className="font-mono text-text-muted">{rs.old_regime}</span>
                    <span className="mx-1 text-text-muted">→</span>
                    <span className="font-mono text-accent">{rs.new_regime}</span>
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-text-muted">
                    {rs.confidence != null ? Number(rs.confidence).toFixed(2) : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-text-muted font-mono truncate max-w-[220px]">
                    {rs.wiki_path || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
