import { useState, useMemo, Fragment } from 'react'
import Panel from './Panel'
import { SkeletonTable, SkeletonChart, SkeletonStrategyCard } from './Skeleton'
import EmptyState from './EmptyState'
import HeroBanner from './HeroBanner'
import { useApi } from '../hooks/useApi'
import { useApiLive } from '../hooks/useApiLive'
import EquityLWChart from './chart/EquityLWChart'

const PERIODS = ['1D', '1W', '1M', 'All']

function EquityChart() {
  const [period, setPeriod] = useState('1D')
  const { data, loading } = useApi(`/api/equity-history?period=${period}&timeframe=5Min`, 60000)

  if (loading && !data) return <SkeletonChart />
  if (!data || !data.timestamps || data.timestamps.length === 0) {
    return (
      <EmptyState
        title="Equity history loading"
        hint="Updates every minute from the Alpaca account equity snapshot."
      />
    )
  }

  return (
    <div>
      <div className="flex gap-1 mb-3">
        {PERIODS.map(p => (
          <button key={p} onClick={() => setPeriod(p)}
            className={`px-2 py-0.5 text-[10px] border rounded transition-colors ${
              p === period
                ? 'bg-accent text-bg-surface border-accent'
                : 'bg-bg-primary text-text-secondary border-border hover:border-text-muted'
            }`}>{p}</button>
        ))}
      </div>
      <EquityLWChart key={period} data={data} height={200} />
    </div>
  )
}

// / phase 7 tier 2j: compact realized-pnl-today breakdown, fills the left column
// / bottom under the equity curve. groups today's filled trades by strategy_id and
// / sums pnl per group, sorted desc.
function DailyPnLByStrategy({ trades }) {
  const rows = useMemo(() => {
    if (!Array.isArray(trades) || trades.length === 0) return []
    const today = new Date().toISOString().slice(0, 10)
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

  if (rows.length === 0) {
    return (
      <EmptyState
        title="No realized P&L today"
        hint="Rows appear as strategies close positions during the session."
      />
    )
  }

  const maxAbs = Math.max(...rows.map(r => Math.abs(r.pnl)), 1)

  return (
    <div className="space-y-1">
      {rows.map(r => {
        const pct = (Math.abs(r.pnl) / maxAbs) * 100
        const colorClass = r.pnl >= 0 ? 'pnl-positive' : 'pnl-negative'
        const barBg = r.pnl >= 0 ? 'bg-win/20' : 'bg-loss/20'
        return (
          <div key={r.sid} className="text-xs flex items-center gap-2">
            <div className="font-mono w-28 truncate text-text-secondary" title={r.sid}>
              {r.sid}
            </div>
            <div className="flex-1 h-4 bg-bg-primary rounded relative overflow-hidden">
              <div className={`absolute inset-y-0 ${r.pnl >= 0 ? 'left-1/2' : 'right-1/2'} ${barBg}`}
                   style={{ width: `${pct / 2}%` }} />
              <div className="absolute inset-0 flex items-center justify-center text-[10px] text-text-muted">
                {r.count} trade{r.count !== 1 ? 's' : ''}
              </div>
            </div>
            <div className={`font-mono w-20 text-right ${colorClass}`}>
              {r.pnl >= 0 ? '+' : ''}${r.pnl.toFixed(2)}
            </div>
          </div>
        )
      })}
    </div>
  )
}


function PositionsTable({ positions, loading }) {
  if (loading) return <SkeletonTable rows={3} cols={6} />
  if (!positions || positions.length === 0) {
    return (
      <EmptyState
        title="No open positions"
        hint="System is watching — first signal arrives after the next strategy evaluation cycle."
      />
    )
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-text-secondary text-[11px] uppercase">
          <th className="text-left px-2 py-1">Symbol</th>
          <th className="text-left px-2 py-1">Side</th>
          <th className="text-right px-2 py-1">Qty</th>
          <th className="text-right px-2 py-1">Entry</th>
          <th className="text-right px-2 py-1">Price</th>
          <th className="text-right px-2 py-1">P&L</th>
        </tr>
      </thead>
      <tbody>
        {positions.map((p, i) => {
          const pl = parseFloat(p.unrealized_pl || 0)
          return (
            <tr key={i} className="hover:bg-bg-hover border-t border-border" style={{ height: 36 }}>
              <td className="px-2 py-1 font-mono font-semibold">{p.symbol}</td>
              <td className={`px-2 py-1 ${p.side === 'long' || p.side === 'buy' ? 'pnl-positive' : 'pnl-negative'}`}>
                {p.side?.toUpperCase()}
              </td>
              <td className="px-2 py-1 text-right font-mono">{parseFloat(p.qty).toFixed(0)}</td>
              <td className="px-2 py-1 text-right font-mono">
                ${parseFloat(p.entry_price || p.price || 0).toFixed(2)}
              </td>
              <td className="px-2 py-1 text-right font-mono">
                ${parseFloat(p.current_price || 0).toFixed(2)}
              </td>
              <td className={`px-2 py-1 text-right font-mono font-semibold ${pl >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
                {pl >= 0 ? '+$' : '-$'}{Math.abs(pl).toFixed(2)}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// / status badge — live / paper_trading / killed / testing / failed mapped to semantic colors
function StatusBadge({ status }) {
  const normalised = (status || '').toLowerCase()
  const map = {
    live: 'chip-positive',
    active: 'chip-positive',
    paper: 'chip-accent',
    paper_trading: 'chip-accent',
    testing: 'chip-warning',
    killed: 'chip-negative',
    failed: 'chip-negative',
  }
  const cls = map[normalised] || 'chip-neutral'
  return (
    <span className={`chip ${cls}`}>
      {status || 'unknown'}
    </span>
  )
}

// / decay badge — shows health at a glance
function DecayBadge({ decay }) {
  if (!decay) return <span className="text-text-muted text-[10px]">—</span>
  const isDecayed = decay.recommendation === 'kill' || decay.recommendation === 'retire' || decay.days_below > 0
  if (!isDecayed) return <span className="chip chip-positive">ok</span>
  const tooltip = `CUSUM: ${decay.cusum != null ? parseFloat(decay.cusum).toFixed(2) : '—'} | days below: ${decay.days_below ?? '—'} | rec: ${decay.recommendation || '—'}`
  return (
    <span title={tooltip} className="chip chip-negative">
      {decay.recommendation || 'decay'}
    </span>
  )
}

// / single strategy card — header (name + status + composite) / 2x2 body / footer
function StrategyCard({ strategy, decay }) {
  const sharpe = strategy.sharpe_ratio != null ? parseFloat(strategy.sharpe_ratio) : null
  const sortino = strategy.sortino_ratio != null ? parseFloat(strategy.sortino_ratio) : null
  const dd = strategy.max_drawdown != null ? parseFloat(strategy.max_drawdown) : null
  const wr = parseFloat(strategy.win_rate || 0)
  const brier = strategy.brier_score != null ? parseFloat(strategy.brier_score) : null
  const comp = parseFloat(strategy.composite_score || 0)
  const trades = strategy.trades_count ?? strategy.total_trades ?? null
  const compColor = comp >= 70 ? 'pnl-positive' : comp >= 40 ? 'text-warning' : 'pnl-negative'

  return (
    <div className="bg-bg-primary border border-border rounded p-3 hover:border-border/70 transition-colors">
      {/* header */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="min-w-0 flex-1">
          <div className="font-mono text-sm font-semibold text-text-primary truncate" title={strategy.strategy_id}>
            {strategy.strategy_id}
          </div>
          <div className="mt-1">
            <StatusBadge status={strategy.status} />
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="type-metric-label">Composite</div>
          <div className={`text-2xl font-mono font-bold leading-tight ${compColor}`}>
            {comp.toFixed(1)}
          </div>
        </div>
      </div>

      {/* 2x2 metric grid */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div>
          <div className="type-metric-label">Sharpe</div>
          <div className={`font-mono text-sm font-semibold ${sharpe == null ? 'text-text-muted' : sharpe >= 1 ? 'pnl-positive' : sharpe < 0 ? 'pnl-negative' : 'text-text-primary'}`}>
            {sharpe == null ? '—' : sharpe.toFixed(2)}
          </div>
        </div>
        <div>
          <div className="type-metric-label">Sortino</div>
          <div className={`font-mono text-sm font-semibold ${sortino == null ? 'text-text-muted' : sortino >= 1 ? 'pnl-positive' : sortino < 0 ? 'pnl-negative' : 'text-text-primary'}`}>
            {sortino == null ? '—' : sortino.toFixed(2)}
          </div>
        </div>
        <div>
          <div className="type-metric-label">Max DD</div>
          <div className={`font-mono text-sm font-semibold ${dd == null ? 'text-text-muted' : dd > -0.1 ? 'pnl-positive' : dd > -0.2 ? 'text-warning' : 'pnl-negative'}`}>
            {dd == null ? '—' : `${(dd * 100).toFixed(1)}%`}
          </div>
        </div>
        <div>
          <div className="type-metric-label">Win Rate</div>
          <div className={`font-mono text-sm font-semibold ${wr >= 0.5 ? 'pnl-positive' : 'text-text-primary'}`}>
            {(wr * 100).toFixed(0)}%
          </div>
        </div>
      </div>

      {/* footer */}
      <div className="flex items-center justify-between pt-2 border-t border-border text-[11px]">
        <div className="flex items-center gap-3">
          <div className="text-text-muted">
            <span className="font-mono text-text-secondary">{trades != null ? trades : '—'}</span> trades
          </div>
          <div
            className="text-text-muted"
            title={brier == null ? 'Brier score computes after 3 closed trades — this strategy has no sells yet' : undefined}
          >
            Brier <span className={`font-mono ${brier == null ? 'text-text-muted' : brier < 0.1 ? 'pnl-positive' : brier < 0.2 ? 'text-text-primary' : brier < 0.3 ? 'text-warning' : 'pnl-negative'}`}>
              {brier == null ? '—' : brier.toFixed(3)}
            </span>
          </div>
        </div>
        <DecayBadge decay={decay} />
      </div>
    </div>
  )
}

function StrategiesPanel({ strategies, loading }) {
  // / fetch decay data in parallel — merged by strategy_id
  const { data: decayData } = useApiLive('/api/strategy-decay', 60000, ['strategy_status_change'])
  const decayById = useMemo(() => {
    const m = {}
    if (Array.isArray(decayData)) {
      for (const d of decayData) m[d.strategy_id] = d
    }
    return m
  }, [decayData])

  if (loading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {Array.from({ length: 6 }).map((_, i) => <SkeletonStrategyCard key={i} />)}
      </div>
    )
  }
  if (!strategies || strategies.length === 0) {
    return (
      <EmptyState
        title="No strategies loaded"
        hint="Strategy configs in configs/strategies/*.json will be loaded on next scheduler tick."
      />
    )
  }

  // / phase 6 step 11: hard cap raised from 18 to 30 now that we ship 29 strategies.
  // / show-all by default; the grid is cheap to render and hiding rows silently was confusing.
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {strategies.slice(0, 30).map((s, i) => (
        <StrategyCard key={s.strategy_id || i} strategy={s} decay={decayById[s.strategy_id]} />
      ))}
    </div>
  )
}

// / merge strategy positions by symbol — tracked rows collapse `untracked` siblings
// / as an expandable sub-row instead of a separate line.
function groupedStrategyPositions(positions) {
  if (!Array.isArray(positions)) return []
  // / bucket by symbol
  const bySymbol = new Map()
  for (const p of positions) {
    const sym = p.symbol
    if (!bySymbol.has(sym)) bySymbol.set(sym, [])
    bySymbol.get(sym).push(p)
  }
  const rows = []
  for (const [sym, entries] of bySymbol) {
    const tracked = entries.filter(e => (e.strategy_id || '').toLowerCase() !== 'untracked' && e.strategy_id)
    const untracked = entries.filter(e => !(e.strategy_id) || (e.strategy_id || '').toLowerCase() === 'untracked')
    if (tracked.length > 0) {
      // / use the first tracked row as primary; stash the untracked siblings
      rows.push({ ...tracked[0], extras: [...tracked.slice(1), ...untracked] })
    } else if (untracked.length > 0) {
      rows.push({ ...untracked[0], extras: untracked.slice(1) })
    }
  }
  return rows
}

function StrategyPositions() {
  const { data: positions, loading } = useApiLive('/api/strategy-positions', 30000, ['position_update', 'trade_executed'])
  const [expanded, setExpanded] = useState({})

  const rows = useMemo(() => groupedStrategyPositions(positions), [positions])

  if (loading && !positions) return <SkeletonTable rows={3} cols={4} />
  if (!rows || rows.length === 0) {
    return (
      <EmptyState
        title="No strategy positions"
        hint="Positions linked to specific strategies will appear here once the first signal executes."
      />
    )
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-text-secondary text-[11px] uppercase">
          <th className="text-left px-2 py-1 w-5"></th>
          <th className="text-left px-2 py-1">Symbol</th>
          <th className="text-left px-2 py-1">Strategy</th>
          <th className="text-right px-2 py-1">Qty</th>
          <th className="text-right px-2 py-1">Avg Entry</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p, i) => {
          const key = `${p.symbol}-${p.strategy_id || 'untracked'}-${i}`
          const hasExtras = p.extras && p.extras.length > 0
          const isOpen = expanded[key]
          return (
            <Fragment key={key}>
              <tr
                className={`hover:bg-bg-hover border-t border-border ${hasExtras ? 'cursor-pointer' : ''}`}
                style={{ height: 36 }}
                onClick={() => hasExtras && setExpanded({ ...expanded, [key]: !isOpen })}
              >
                <td className="px-2 py-1 text-text-muted text-[10px]">
                  {hasExtras ? (isOpen ? '▾' : '▸') : ''}
                </td>
                <td className="px-2 py-1 font-mono font-semibold">{p.symbol}</td>
                <td className="px-2 py-1 text-text-secondary truncate max-w-[160px]">
                  {p.strategy_id || 'untracked'}
                  {hasExtras && (
                    <span className="ml-2 chip chip-neutral">+{p.extras.length}</span>
                  )}
                </td>
                <td className="px-2 py-1 text-right font-mono">{p.qty}</td>
                <td className="px-2 py-1 text-right font-mono">${parseFloat(p.avg_entry_price || 0).toFixed(2)}</td>
              </tr>
              {hasExtras && isOpen && p.extras.map((ex, j) => (
                <tr key={`${key}-x-${j}`} className="border-t border-border/50 bg-bg-primary/30" style={{ height: 32 }}>
                  <td className="px-2 py-1"></td>
                  <td className="px-2 py-1 pl-6 text-text-muted text-[11px]">↳</td>
                  <td className="px-2 py-1 text-text-muted truncate max-w-[160px]">
                    {ex.strategy_id || 'untracked'}
                  </td>
                  <td className="px-2 py-1 text-right font-mono text-text-muted">{ex.qty}</td>
                  <td className="px-2 py-1 text-right font-mono text-text-muted">${parseFloat(ex.avg_entry_price || 0).toFixed(2)}</td>
                </tr>
              ))}
            </Fragment>
          )
        })}
      </tbody>
    </table>
  )
}

// / correlation heatmap — colored cells by correlation strength
// / green = low abs correlation (diversification); red = high (concentration risk)
function CorrelationHeatmap() {
  const { data, loading, error } = useApiLive('/api/portfolio/correlation', 60000, ['position_update'])

  if (loading && !data) return <SkeletonChart />
  if (error) return <div className="text-loss text-sm py-4">Failed to load: {error}</div>
  if (!data || !data.symbols || data.symbols.length === 0) {
    return (
      <EmptyState
        title="Not enough positions for correlation"
        hint="Correlation matrix needs at least 2 open positions to compute pairwise returns."
      />
    )
  }

  const symbols = data.symbols
  const matrix = data.matrix || []

  const cellBg = (v) => {
    const n = typeof v === 'number' ? v : parseFloat(v)
    if (!Number.isFinite(n)) return 'rgba(100,100,120,0.2)'
    const abs = Math.min(1, Math.abs(n))
    if (abs < 0.3) return `rgba(0, 220, 130, ${0.15 + abs * 0.3})`
    if (abs < 0.6) return `rgba(245, 158, 11, ${0.3 + (abs - 0.3) * 0.6})`
    return `rgba(255, 71, 87, ${0.35 + (abs - 0.6) * 1.3})`
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="text-[10px] font-mono border-collapse">
          <thead>
            <tr>
              <th className="p-1"></th>
              {symbols.map(s => (
                <th key={s} className="p-1 text-text-secondary text-center font-semibold min-w-[40px]">
                  {s.length > 6 ? s.slice(0, 6) : s}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {symbols.map((row, i) => (
              <tr key={row}>
                <td className="p-1 text-text-secondary font-semibold text-right pr-2">{row.length > 6 ? row.slice(0, 6) : row}</td>
                {symbols.map((col, j) => {
                  const v = matrix[i]?.[j]
                  const n = typeof v === 'number' ? v : parseFloat(v)
                  return (
                    <td
                      key={`${i}-${j}`}
                      className="p-1 text-center text-text-primary min-w-[40px]"
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
      </div>
      <div className="flex items-center gap-3 mt-3 text-[10px] text-text-muted">
        <span>low |ρ|</span>
        <div className="flex-1 h-1.5 rounded" style={{ background: 'linear-gradient(to right, rgba(0,220,130,0.4), rgba(245,158,11,0.6), rgba(255,71,87,0.9))' }} />
        <span>high |ρ|</span>
      </div>
    </div>
  )
}

// / sector concentration — horizontal bars; highlight sectors >30% of equity
function SectorConcentration() {
  const { data, loading, error } = useApiLive('/api/portfolio/sectors', 60000, ['position_update'])

  if (loading && !data) return <SkeletonTable rows={5} cols={2} />
  if (error) return <div className="text-loss text-sm py-4">Failed to load: {error}</div>
  const sectors = Array.isArray(data) ? data : data?.sectors
  if (!Array.isArray(sectors) || sectors.length === 0) {
    return (
      <EmptyState
        title="No sector breakdown"
        hint="Sector concentration appears once positions have sector metadata populated."
      />
    )
  }

  const rows = [...sectors].sort((a, b) => (parseFloat(b.pct_of_portfolio ?? b.pct_of_equity) || 0) - (parseFloat(a.pct_of_portfolio ?? a.pct_of_equity) || 0))
  const maxPct = rows.reduce((m, r) => Math.max(m, parseFloat(r.pct_of_portfolio ?? r.pct_of_equity) || 0), 0) || 1

  return (
    <div className="space-y-2">
      {rows.map(r => {
        const pct = parseFloat(r.pct_of_portfolio ?? r.pct_of_equity) || 0
        const usd = parseFloat(r.value ?? r.value_usd) || 0
        const overConcentrated = pct > 0.30
        const barWidth = (pct / maxPct) * 100
        return (
          <div key={r.sector} className="flex items-center gap-2 text-xs">
            <div className="w-24 text-text-secondary truncate" title={r.sector}>{r.sector}</div>
            <div className="flex-1 h-4 bg-bg-primary border border-border rounded relative">
              <div
                className={`h-full rounded ${overConcentrated ? 'bg-loss' : pct > 0.20 ? 'bg-warning' : 'bg-accent'}`}
                style={{ width: `${barWidth}%` }}
              />
              <div className="absolute inset-0 flex items-center px-2 text-[10px] font-mono">
                <span className={overConcentrated ? 'text-text-primary font-semibold' : 'text-text-primary'}>
                  {(pct * 100).toFixed(1)}%
                </span>
                <span className="ml-auto text-text-muted">${(usd / 1000).toFixed(1)}K</span>
              </div>
            </div>
            {overConcentrated && (
              <span className="chip chip-negative">over</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

// / tail dependence — single big number + color + optional per-position breakdown
function TailDependenceCard() {
  const { data, loading, error } = useApiLive('/api/portfolio/tail-dependence', 60000, ['position_update'])

  if (loading && !data) return <div className="skeleton h-20 w-full rounded" />
  if (error) return <div className="text-loss text-sm py-4">Failed to load: {error}</div>
  const lamRaw = data?.lambda_lower ?? data?.aggregated_lambda
  if (!data || lamRaw == null) {
    return (
      <EmptyState
        title="No tail dependence yet"
        hint="Needs ≥2 positions — the copula fit measures crash co-movement between assets."
      />
    )
  }

  const lam = parseFloat(lamRaw) || 0
  const threshold = parseFloat(data.threshold) || 0.3
  const color = lam < 0.2 ? 'pnl-positive' : lam < threshold ? 'text-warning' : 'pnl-negative'
  const label = lam < 0.2 ? 'LOW' : lam < threshold ? 'ELEVATED' : 'HIGH'
  const borderColor = lam < 0.2 ? 'border-l-profit' : lam < threshold ? 'border-l-warning' : 'border-l-loss'
  const perPos = Array.isArray(data.per_position) ? data.per_position : []

  return (
    <div className="space-y-3">
      <div className={`bg-bg-primary border border-border border-l-4 ${borderColor} p-3 rounded`}>
        <div className="type-metric-label">Aggregated λ (lower tail)</div>
        <div className="flex items-baseline gap-3 mt-1">
          <div className={`text-3xl font-mono font-bold ${color}`}>{lam.toFixed(3)}</div>
          <div className={`text-xs uppercase font-semibold ${color}`}>{label}</div>
        </div>
        <div className="text-[10px] text-text-muted mt-1">
          {lam < 0.2 && 'portfolio well-diversified against crash co-movement'}
          {lam >= 0.2 && lam < 0.3 && 'some crash correlation — monitor new entries'}
          {lam >= 0.3 && 'high crash co-movement — new entries may be rejected'}
        </div>
      </div>
      {perPos.length > 0 && (
        <div className="space-y-1">
          <div className="type-metric-label">Per-Position</div>
          {perPos.slice(0, 8).map((p) => {
            const v = parseFloat(p.lambda) || 0
            const c = v < 0.2 ? 'pnl-positive' : v < 0.3 ? 'text-warning' : 'pnl-negative'
            return (
              <div key={p.symbol} className="flex items-center justify-between text-xs border-t border-border py-1">
                <span className="font-mono font-semibold">{p.symbol}</span>
                <span className={`font-mono ${c}`}>{v.toFixed(3)}</span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// / hero banner — equity + daily change + positions count from the fetched portfolio
function PortfolioHero({ portfolio }) {
  const equity = portfolio?.equity
  const pnl = portfolio?.daily_pnl || 0
  const pnlPct = equity > 0 ? (pnl / (equity - pnl)) * 100 : 0
  const openCount = portfolio?.positions_count ?? portfolio?.positions?.length ?? '—'
  const cash = portfolio?.cash
  const buyingPower = portfolio?.buying_power
  const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative'

  return (
    <HeroBanner>
      <div className="hero-metric">
        <span className="hero-metric-label">Equity</span>
        <span className="hero-metric-value font-mono">
          {equity != null
            ? `$${equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
            : '—'}
        </span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Daily change</span>
        <span className={`hero-metric-value font-mono ${pnlClass}`}>
          {pnl >= 0 ? '+$' : '-$'}{Math.abs(pnl).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          <span className="ml-1 text-xs font-normal">({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)</span>
        </span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Open positions</span>
        <span className="hero-metric-value font-mono">{openCount}</span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Cash</span>
        <span className="hero-metric-value-sm font-mono">
          {cash != null ? `${cash < 0 ? '-$' : '$'}${Math.abs(cash).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}
        </span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Buying power</span>
        <span className="hero-metric-value-sm font-mono">
          {buyingPower != null ? `$${buyingPower.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}
        </span>
      </div>
    </HeroBanner>
  )
}

export default function PortfolioTab({ portfolio, trades, strategies, loading }) {
  return (
    <div className="space-y-6">
      {/* hero */}
      <PortfolioHero portfolio={portfolio} />

      {/* primary positions + equity. stacks equity chart + quick stats tile on
          the left so it balances against the (usually longer) positions table */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="flex flex-col gap-4">
          <Panel title="Equity Curve">
            <EquityChart />
          </Panel>
          <Panel title="Today's P&L by Strategy">
            <DailyPnLByStrategy trades={trades} />
          </Panel>
        </div>

        <Panel title="Open Positions">
          <PositionsTable positions={portfolio?.positions} loading={loading.portfolio} />
        </Panel>
      </div>

      {/* strategy section — full width card grid */}
      <Panel title="Strategy Scores">
        <StrategiesPanel strategies={strategies} loading={loading.strategies} />
      </Panel>

      <Panel title="Strategy Positions">
        <StrategyPositions />
      </Panel>

      {/* risk row — uniform 3-col layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Panel title="Portfolio Tail Dependence">
          <TailDependenceCard />
        </Panel>
        <Panel title="Sector Concentration">
          <SectorConcentration />
        </Panel>
        <Panel title="Correlation Heatmap">
          <CorrelationHeatmap />
        </Panel>
      </div>
    </div>
  )
}
