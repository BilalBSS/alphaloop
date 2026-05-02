import { useMemo } from 'react'
import Card from '../ui/Card'
import { useApiLive } from '../../hooks/useApiLive'

// / sector mix by nav

const CAP = 0.35

function fmtUsd(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—'
  const v = Number(n)
  return `${v < 0 ? '−$' : '$'}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

export default function SectorMix() {
  const { data, loading } = useApiLive('/api/portfolio/sectors', 60000, ['position_update'])

  const rows = useMemo(() => {
    const arr = Array.isArray(data) ? data : data?.sectors
    if (!Array.isArray(arr)) return []
    return [...arr]
      .map((r) => ({
        sector: r.sector,
        pct: Number(r.pct_of_portfolio ?? r.pct_of_equity ?? 0),
        usd: Number(r.value ?? r.value_usd ?? 0),
      }))
      .sort((a, b) => b.pct - a.pct)
  }, [data])

  const maxPct = rows.reduce((m, r) => Math.max(m, r.pct), 0) || 1

  return (
    <Card title={<><b>sector</b> mix</>} meta={`% of NAV · cap ${Math.round(CAP * 100)}%`} p0>
      {loading && rows.length === 0 ? (
        <div className="empty-state"><div className="empty-state-title">loading sectors</div></div>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">no sector breakdown</div>
          <div className="empty-state-hint">appears once positions have sector metadata.</div>
        </div>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>sector</th>
              <th className="r">$</th>
              <th>weight</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const over = r.pct > CAP
              const tone = over ? 'neg' : r.pct > 0.20 ? 'warn' : 'acc'
              const barW = Math.min(100, (r.pct / maxPct) * 100)
              return (
                <tr key={r.sector}>
                  <td>{r.sector}</td>
                  <td className="r dim">{fmtUsd(r.usd)}</td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ flex: 1, height: 4, background: 'var(--bg-3)', border: '1px solid var(--line)', position: 'relative' }}>
                        <div
                          style={{
                            position: 'absolute',
                            left: 0,
                            top: 0,
                            bottom: 0,
                            width: `${barW}%`,
                            background: tone === 'neg' ? 'var(--neg)' : tone === 'warn' ? 'var(--warn)' : 'var(--acc)',
                          }}
                        />
                      </div>
                      <span className={over ? 'neg' : ''} style={{ minWidth: 52, textAlign: 'right', fontSize: 11 }}>
                        {(r.pct * 100).toFixed(1)}%
                      </span>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Card>
  )
}
