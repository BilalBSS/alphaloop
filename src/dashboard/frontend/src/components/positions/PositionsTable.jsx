import { useMemo } from 'react'
import Card from '../ui/Card'
import Pill from '../ui/Pill'
import { useApiLive } from '../../hooks/useApiLive'

// / open positions table

function fmtUsd(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—'
  const v = Number(n)
  const sign = v < 0 ? '−$' : '$'
  return `${sign}${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

function fmtPct(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—'
  const v = Number(n) * 100
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`
}

function fmtAge(p) {
  const ts = p.opened_at ?? p.entry_time ?? p.created_at
  if (!ts) return '—'
  const diffMs = Date.now() - new Date(ts).getTime()
  if (!Number.isFinite(diffMs) || diffMs < 0) return '—'
  const days = Math.floor(diffMs / 86400000)
  if (days >= 1) return `${days}d`
  const hours = Math.floor(diffMs / 3600000)
  if (hours >= 1) return `${hours}h`
  return `${Math.floor(diffMs / 60000)}m`
}

export default function PositionsTable({ portfolio }) {
  const { data: stratPositions } = useApiLive('/api/strategy-positions', 30000, ['position_update', 'trade_executed'])

  const stratBySymbol = useMemo(() => {
    const m = new Map()
    if (!Array.isArray(stratPositions)) return m
    for (const sp of stratPositions) {
      const sym = sp.symbol
      if (!sym) continue
      if (!m.has(sym)) m.set(sym, [])
      m.get(sym).push(sp)
    }
    return m
  }, [stratPositions])

  const rows = useMemo(() => {
    const positions = portfolio?.positions
    if (!Array.isArray(positions) || positions.length === 0) return []
    return positions.map((p) => {
      const tracked = (stratBySymbol.get(p.symbol) || [])
        .filter((sp) => sp.strategy_id && sp.strategy_id.toLowerCase() !== 'untracked')
      const strategy = tracked[0]?.strategy_id ?? 'untracked'
      const entry = Number(p.entry_price ?? p.avg_entry_price ?? p.price ?? 0)
      const last = Number(p.current_price ?? p.last_price ?? entry)
      const pl = Number(p.unrealized_pl ?? p.unrealized_pnl ?? 0)
      const plPct = entry > 0 ? (last - entry) / entry : null
      return {
        symbol: p.symbol,
        side: (p.side || '').toLowerCase(),
        qty: p.qty,
        entry,
        last,
        pl,
        plPct,
        strategy,
        sector: p.sector ?? '—',
        held: fmtAge(p),
      }
    })
  }, [portfolio, stratBySymbol])

  return (
    <Card p0>
      {rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">no open positions</div>
          <div className="empty-state-hint">first signal arrives after the next strategy evaluation cycle.</div>
        </div>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>symbol</th>
              <th>side</th>
              <th className="r">qty</th>
              <th className="r">entry</th>
              <th className="r">last</th>
              <th className="r">P/L</th>
              <th className="r">P/L %</th>
              <th>strategy</th>
              <th>sector</th>
              <th className="r">held</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const plClass = r.pl >= 0 ? 'pos' : 'neg'
              const sideVariant = r.side === 'long' || r.side === 'buy' ? 'long' : 'short'
              return (
                <tr key={`${r.symbol}-${i}`}>
                  <td className="sym">{r.symbol}</td>
                  <td><Pill variant={sideVariant}>{r.side || '—'}</Pill></td>
                  <td className="r">{Number(r.qty).toLocaleString()}</td>
                  <td className="r">{fmtUsd(r.entry)}</td>
                  <td className="r">{fmtUsd(r.last)}</td>
                  <td className={`r ${plClass}`}>{fmtUsd(r.pl)}</td>
                  <td className={`r ${plClass}`}>{fmtPct(r.plPct)}</td>
                  <td className="dim">{r.strategy}</td>
                  <td className="dim">{r.sector}</td>
                  <td className="r tiny">{r.held}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Card>
  )
}
