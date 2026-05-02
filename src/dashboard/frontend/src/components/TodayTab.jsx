import { useState, useMemo } from 'react'
import SectionH from './ui/SectionH'
import Card from './ui/Card'
import { useApi } from '../hooks/useApi'
import EquityLWChart from './chart/EquityLWChart'
import RegimeTimelineCard from './today/RegimeTimeline'
import ActivityStream from './today/ActivityStream'
import SignalFunnelCard from './today/SignalFunnelCard'
import SynthesisCard from './today/SynthesisCard'

const PERIODS = ['1D', '1W', '1M', 'All']

function todayDate() {
  return new Date().toISOString().slice(0, 10)
}

function fmtUsd(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—'
  const v = Number(n)
  return `${v < 0 ? '−$' : '$'}${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

function fmtPct(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—'
  const v = Number(n) * 100
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`
}

function EquityCard() {
  const [period, setPeriod] = useState('1D')
  const { data, loading } = useApi(`/api/equity-history?period=${period}&timeframe=5Min`, 60000)
  const hasData = data?.timestamps?.length > 0

  return (
    <Card title={<><b>equity curve</b> · {period}</>} meta="alpaca account snapshot · 1m">
      <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
        {PERIODS.map((p) => {
          const active = p === period
          return (
            <button
              key={p}
              type="button"
              className="btn"
              onClick={() => setPeriod(p)}
              style={{
                fontSize: 10.5,
                padding: '3px 9px',
                color: active ? 'var(--acc)' : undefined,
                borderColor: active ? 'var(--acc)' : undefined,
                background: active ? 'var(--bg-3)' : undefined,
              }}
            >
              {p}
            </button>
          )
        })}
      </div>
      {loading && !hasData ? (
        <div className="empty-state"><div className="empty-state-title">loading equity</div></div>
      ) : !hasData ? (
        <div className="empty-state">
          <div className="empty-state-title">no equity history</div>
          <div className="empty-state-hint">populated from alpaca account snapshot once the broker is live.</div>
        </div>
      ) : (
        <div style={{ height: 200 }}>
          <EquityLWChart key={period} data={data} height={200} />
        </div>
      )}
    </Card>
  )
}

function DailyPnLCard({ trades }) {
  const rows = useMemo(() => {
    if (!Array.isArray(trades) || trades.length === 0) return []
    const today = todayDate()
    const byStrat = new Map()
    for (const t of trades) {
      const ts = t.created_at || t.timestamp || ''
      if (!ts.startsWith(today)) continue
      const sid = t.strategy_id || '(unassigned)'
      const pnl = parseFloat(t.pnl ?? 0) || 0
      const cur = byStrat.get(sid) || { pnl: 0, count: 0 }
      byStrat.set(sid, { pnl: cur.pnl + pnl, count: cur.count + 1 })
    }
    return [...byStrat.entries()]
      .map(([sid, v]) => ({ sid, pnl: v.pnl, count: v.count }))
      .sort((a, b) => b.pnl - a.pnl)
  }, [trades])

  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.pnl)), 1)

  return (
    <Card title={<><b>realized P/L</b> · today</>} meta="grouped by strategy">
      {rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">no realized P/L today</div>
          <div className="empty-state-hint">rows appear as strategies close positions during the session.</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {rows.map((r) => {
            const pct = (Math.abs(r.pnl) / maxAbs) * 100
            const tone = r.pnl >= 0 ? 'pos' : 'neg'
            return (
              <div key={r.sid} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, fontFamily: 'var(--mono)' }}>
                <div style={{ width: 130, color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.sid}>
                  {r.sid}
                </div>
                <div style={{ flex: 1, height: 14, background: 'var(--bg-3)', border: '1px solid var(--line)', position: 'relative' }}>
                  <div
                    style={{
                      position: 'absolute',
                      top: 0,
                      bottom: 0,
                      [r.pnl >= 0 ? 'left' : 'right']: '50%',
                      width: `${pct / 2}%`,
                      background: tone === 'pos' ? 'var(--pos-dim)' : 'var(--neg-dim)',
                    }}
                  />
                  <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, color: 'var(--ink-4)' }}>
                    {r.count} trade{r.count !== 1 ? 's' : ''}
                  </div>
                </div>
                <div className={tone} style={{ width: 84, textAlign: 'right' }}>
                  {fmtUsd(r.pnl)}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}

function TopMoversCard({ portfolio }) {
  const rows = useMemo(() => {
    const positions = portfolio?.positions
    if (!Array.isArray(positions) || positions.length === 0) return []
    return [...positions]
      .map((p) => {
        const entry = Number(p.entry_price ?? p.avg_entry_price ?? p.price ?? 0)
        const last = Number(p.current_price ?? p.last_price ?? entry)
        const pl = Number(p.unrealized_pl ?? p.unrealized_pnl ?? 0)
        const plPct = entry > 0 ? (last - entry) / entry : 0
        return {
          symbol: p.symbol,
          side: (p.side || '').toLowerCase(),
          qty: p.qty,
          entry,
          last,
          pl,
          plPct,
          strategy: p.strategy_id ?? '—',
        }
      })
      .sort((a, b) => Math.abs(b.plPct) - Math.abs(a.plPct))
  }, [portfolio])

  const total = portfolio?.positions_count ?? rows.length

  return (
    <Card title={<><b>positions</b> · top movers</>} meta={`${rows.length} of ${total}`} p0>
      {rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">no open positions</div>
          <div className="empty-state-hint">first signal arrives after the next strategy evaluation cycle.</div>
        </div>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>sym</th>
              <th>side</th>
              <th className="r">qty</th>
              <th className="r">entry</th>
              <th className="r">last</th>
              <th className="r">P/L</th>
              <th className="r">%</th>
              <th>strategy</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 8).map((r, i) => {
              const tone = r.pl >= 0 ? 'pos' : 'neg'
              return (
                <tr key={`${r.symbol}-${i}`}>
                  <td className="sym">{r.symbol}</td>
                  <td className={r.side === 'long' || r.side === 'buy' ? 'pos' : 'neg'}>{r.side || '—'}</td>
                  <td className="r">{Number(r.qty).toLocaleString()}</td>
                  <td className="r">{fmtUsd(r.entry)}</td>
                  <td className="r">{fmtUsd(r.last)}</td>
                  <td className={`r ${tone}`}>{fmtUsd(r.pl)}</td>
                  <td className={`r ${tone}`}>{fmtPct(r.plPct)}</td>
                  <td className="dim">{r.strategy}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Card>
  )
}

export default function TodayTab({ portfolio, trades }) {
  return (
    <SectionH
      num="01"
      title="today"
      em={`session ${todayDate()}`}
      meta={<><code>/api/portfolio</code> · <code>/api/regime-timeline</code> · ws fanout</>}
    >
      <RegimeTimelineCard market="equity" days={90} />

      <div className="grid c12-7-5">
        <TopMoversCard portfolio={portfolio} />
        <ActivityStream />
      </div>

      <div className="grid c2" style={{ marginTop: 18 }}>
        <SynthesisCard />
        <SignalFunnelCard hours={24} />
      </div>

      <div className="grid c2" style={{ marginTop: 18 }}>
        <EquityCard />
        <DailyPnLCard trades={trades} />
      </div>
    </SectionH>
  )
}
