import { useState, useMemo } from 'react'
import Panel from './Panel'
import { SkeletonTable, SkeletonChart } from './Skeleton'
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
      <div className="flex items-center justify-center h-48 text-text-muted text-sm">
        Equity history loading — updates every minute
      </div>
    )
  }

  return (
    <div>
      <div className="flex gap-1 mb-2">
        {PERIODS.map(p => (
          <button key={p} onClick={() => setPeriod(p)}
            className={`px-2 py-0.5 text-[10px] border rounded ${
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

function PortfolioSummary({ portfolio }) {
  if (!portfolio || !portfolio.equity) return null
  const pnl = portfolio.daily_pnl || 0
  const pnlPct = portfolio.equity > 0 ? (pnl / (portfolio.equity - pnl)) * 100 : 0
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] mb-2">
      <div className="bg-bg-primary border border-border p-2">
        <div className="text-[10px] uppercase text-text-muted">Equity</div>
        <div className="text-lg font-mono font-bold text-text-primary">
          ${portfolio.equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </div>
      </div>
      <div className="bg-bg-primary border border-border p-2">
        <div className="text-[10px] uppercase text-text-muted">Daily P&L</div>
        <div className={`text-lg font-mono font-bold ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
          {pnl >= 0 ? '+$' : '-$'}{Math.abs(pnl).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          <span className="text-xs ml-1">({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)</span>
        </div>
      </div>
      <div className="bg-bg-primary border border-border p-2">
        <div className="text-[10px] uppercase text-text-muted">Cash</div>
        <div className="text-lg font-mono font-bold text-text-primary">
          {portfolio.cash != null ? `${portfolio.cash < 0 ? '-$' : '$'}${Math.abs(portfolio.cash).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '--'}
        </div>
      </div>
      <div className="bg-bg-primary border border-border p-2">
        <div className="text-[10px] uppercase text-text-muted">Buying Power</div>
        <div className="text-lg font-mono font-bold text-text-primary">
          ${portfolio.buying_power?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) || '--'}
        </div>
      </div>
    </div>
  )
}

function PositionsTable({ positions, loading }) {
  if (loading) return <SkeletonTable rows={3} cols={6} />
  if (!positions || positions.length === 0) {
    return <div className="text-text-muted text-sm py-4">No open positions — system is watching</div>
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
              <td className={`px-2 py-1 ${p.side === 'long' || p.side === 'buy' ? 'text-profit' : 'text-loss'}`}>
                {p.side?.toUpperCase()}
              </td>
              <td className="px-2 py-1 text-right font-mono">{parseFloat(p.qty).toFixed(0)}</td>
              <td className="px-2 py-1 text-right font-mono">
                ${parseFloat(p.entry_price || p.price || 0).toFixed(2)}
              </td>
              <td className="px-2 py-1 text-right font-mono">
                ${parseFloat(p.current_price || 0).toFixed(2)}
              </td>
              <td className={`px-2 py-1 text-right font-mono font-semibold ${pl >= 0 ? 'text-profit' : 'text-loss'}`}>
                {pl >= 0 ? '+$' : '-$'}{Math.abs(pl).toFixed(2)}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function RecentTrades({ trades, loading }) {
  if (loading) return <SkeletonTable rows={3} cols={4} />
  if (!trades || trades.length === 0) {
    return <div className="text-text-muted text-sm py-4">No trades yet — first signal pending</div>
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-text-secondary text-[11px] uppercase">
          <th className="text-left px-2 py-1">Symbol</th>
          <th className="text-left px-2 py-1">Side</th>
          <th className="text-right px-2 py-1">P&L</th>
          <th className="text-right px-2 py-1">Time</th>
        </tr>
      </thead>
      <tbody>
        {trades.slice(0, 10).map((t, i) => {
          const pnl = t.pnl != null ? parseFloat(t.pnl) : null
          return (
            <tr key={i} className="hover:bg-bg-hover border-t border-border" style={{ height: 36 }}>
              <td className="px-2 py-1 font-mono font-semibold">{t.symbol}</td>
              <td className={`px-2 py-1 ${t.side === 'buy' ? 'text-profit' : 'text-loss'}`}>
                {t.side?.toUpperCase()}
              </td>
              <td className={`px-2 py-1 text-right font-mono ${pnl !== null && pnl >= 0 ? 'text-profit' : pnl !== null ? 'text-loss' : 'text-text-muted'}`}>
                {pnl !== null ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}` : '--'}
              </td>
              <td className="px-2 py-1 text-right text-text-muted">
                {t.created_at?.replace('T', ' ').slice(0, 16) || '--'}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// / status badge colors — live/paper/killed/testing
function StatusBadge({ status }) {
  const map = {
    live: 'bg-profit/20 text-profit border-profit/40',
    paper: 'bg-accent/20 text-accent border-accent/40',
    paper_trading: 'bg-accent/20 text-accent border-accent/40',
    killed: 'bg-loss/20 text-loss border-loss/40',
    testing: 'bg-warning/20 text-warning border-warning/40',
  }
  const cls = map[status] || 'bg-bg-primary text-text-muted border-border'
  return (
    <span className={`px-1.5 py-0.5 text-[9px] uppercase font-semibold border ${cls} rounded`}>
      {status || '--'}
    </span>
  )
}

// / decay badge — red dot if strategy is in decay
function DecayBadge({ decay }) {
  if (!decay) return <span className="text-text-muted text-[10px]">--</span>
  const isDecayed = decay.recommendation === 'kill' || decay.recommendation === 'retire' || decay.days_below > 0
  if (!isDecayed) return <span className="text-profit text-[10px]">ok</span>
  const tooltip = `CUSUM: ${decay.cusum != null ? parseFloat(decay.cusum).toFixed(2) : '--'} | days below: ${decay.days_below ?? '--'} | rec: ${decay.recommendation || '--'}`
  return (
    <span title={tooltip} className="inline-flex items-center gap-1">
      <span className="w-2 h-2 rounded-full bg-loss inline-block" />
      <span className="text-loss text-[10px] uppercase">{decay.recommendation || 'decay'}</span>
    </span>
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

  if (loading) return <SkeletonTable rows={4} cols={9} />
  if (!strategies || strategies.length === 0) {
    return <div className="text-text-muted text-sm py-4">No strategies loaded</div>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary text-[11px] uppercase">
            <th className="text-left px-2 py-1">Strategy</th>
            <th className="text-left px-2 py-1">Status</th>
            <th className="text-right px-2 py-1">Sharpe</th>
            <th className="text-right px-2 py-1">Max DD</th>
            <th className="text-right px-2 py-1">Win</th>
            <th className="text-right px-2 py-1">Brier</th>
            <th className="text-right px-2 py-1">Comp</th>
            <th className="text-right px-2 py-1">Trades</th>
            <th className="text-left px-2 py-1">Decay</th>
          </tr>
        </thead>
        <tbody>
          {strategies.slice(0, 16).map((s, i) => {
            const sharpe = s.sharpe_ratio != null ? parseFloat(s.sharpe_ratio) : null
            const dd = s.max_drawdown != null ? parseFloat(s.max_drawdown) : null
            const wr = parseFloat(s.win_rate || 0)
            const brier = s.brier_score != null ? parseFloat(s.brier_score) : null
            const comp = parseFloat(s.composite_score || 0)
            const trades = s.trades_count ?? s.total_trades ?? null
            const decay = decayById[s.strategy_id]
            return (
              <tr key={i} className="hover:bg-bg-hover border-t border-border" style={{ height: 36 }}>
                <td className="px-2 py-1 font-mono truncate max-w-[140px]" title={s.strategy_id}>
                  {s.strategy_id}
                </td>
                <td className="px-2 py-1">
                  <StatusBadge status={s.status} />
                </td>
                <td className={`px-2 py-1 text-right font-mono ${sharpe == null ? 'text-text-muted' : sharpe >= 1 ? 'text-profit' : sharpe < 0 ? 'text-loss' : ''}`}>
                  {sharpe == null ? '--' : sharpe.toFixed(2)}
                </td>
                <td className={`px-2 py-1 text-right font-mono ${dd == null ? 'text-text-muted' : dd > -0.1 ? 'text-profit' : dd > -0.2 ? 'text-warning' : 'text-loss'}`}>
                  {dd == null ? '--' : `${(dd * 100).toFixed(1)}%`}
                </td>
                <td className={`px-2 py-1 text-right font-mono ${wr >= 0.5 ? 'text-profit' : 'text-text-secondary'}`}>
                  {(wr * 100).toFixed(0)}%
                </td>
                <td className={`px-2 py-1 text-right font-mono ${brier == null ? 'text-text-muted' : brier < 0.1 ? 'text-profit' : brier < 0.2 ? 'text-text-primary' : brier < 0.3 ? 'text-warning' : 'text-loss'}`}>
                  {brier == null ? '--' : brier.toFixed(3)}
                </td>
                <td className={`px-2 py-1 text-right font-mono ${comp >= 70 ? 'text-profit' : comp >= 40 ? 'text-warning' : 'text-loss'}`}>
                  {comp.toFixed(1)}
                </td>
                <td className="px-2 py-1 text-right font-mono text-text-secondary">
                  {trades != null ? trades : '--'}
                </td>
                <td className="px-2 py-1">
                  <DecayBadge decay={decay} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function StrategyPositions() {
  const { data: positions, loading } = useApiLive('/api/strategy-positions', 30000, ['position_update', 'trade_executed'])

  if (loading && !positions) return <SkeletonTable rows={3} cols={4} />
  if (!positions || positions.length === 0) {
    return <div className="text-text-muted text-sm py-4">No strategy positions</div>
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-text-secondary text-[11px] uppercase">
          <th className="text-left px-2 py-1">Symbol</th>
          <th className="text-left px-2 py-1">Strategy</th>
          <th className="text-right px-2 py-1">Qty</th>
          <th className="text-right px-2 py-1">Avg Entry</th>
        </tr>
      </thead>
      <tbody>
        {positions.map((p, i) => (
          <tr key={i} className="hover:bg-bg-hover border-t border-border" style={{ height: 36 }}>
            <td className="px-2 py-1 font-mono font-semibold">{p.symbol}</td>
            <td className="px-2 py-1 text-text-secondary truncate max-w-[120px]">{p.strategy_id || '--'}</td>
            <td className="px-2 py-1 text-right font-mono">{p.qty}</td>
            <td className="px-2 py-1 text-right font-mono">${parseFloat(p.avg_entry_price || 0).toFixed(2)}</td>
          </tr>
        ))}
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
    return <div className="text-text-muted text-sm py-4">Not enough positions for correlation</div>
  }

  const symbols = data.symbols
  const matrix = data.matrix || []

  // / correlation color: 0 = gray, ±1 = red, in-between = warm gradient
  const cellBg = (v) => {
    const n = typeof v === 'number' ? v : parseFloat(v)
    if (!Number.isFinite(n)) return 'rgba(100,100,120,0.2)'
    const abs = Math.min(1, Math.abs(n))
    // / scale from soft green at 0 up through yellow/orange toward red at 1
    if (abs < 0.3) return `rgba(0, 220, 130, ${0.15 + abs * 0.3})`
    if (abs < 0.6) return `rgba(245, 158, 11, ${0.3 + (abs - 0.3) * 0.6})`
    return `rgba(255, 71, 87, ${0.35 + (abs - 0.6) * 1.3})`
  }

  return (
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
                    title={`${row} ↔ ${col}: ${Number.isFinite(n) ? n.toFixed(3) : '--'}`}
                  >
                    {Number.isFinite(n) ? n.toFixed(2) : '--'}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex items-center gap-3 mt-2 text-[10px] text-text-muted">
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
    return <div className="text-text-muted text-sm py-4">No sector breakdown available</div>
  }

  // / normalize + sort by pct desc; max width at the largest bar
  const rows = [...sectors].sort((a, b) => (parseFloat(b.pct_of_portfolio ?? b.pct_of_equity) || 0) - (parseFloat(a.pct_of_portfolio ?? a.pct_of_equity) || 0))
  const maxPct = rows.reduce((m, r) => Math.max(m, parseFloat(r.pct_of_portfolio ?? r.pct_of_equity) || 0), 0) || 1

  return (
    <div className="space-y-1.5">
      {rows.map(r => {
        const pct = parseFloat(r.pct_of_portfolio ?? r.pct_of_equity) || 0
        const usd = parseFloat(r.value ?? r.value_usd) || 0
        const overConcentrated = pct > 0.30
        const barWidth = (pct / maxPct) * 100
        return (
          <div key={r.sector} className="flex items-center gap-2 text-xs">
            <div className="w-24 text-text-secondary truncate" title={r.sector}>{r.sector}</div>
            <div className="flex-1 h-4 bg-bg-primary border border-border relative">
              <div
                className={`h-full ${overConcentrated ? 'bg-loss' : pct > 0.20 ? 'bg-warning' : 'bg-accent'}`}
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
              <span className="text-[9px] uppercase text-loss font-semibold">over</span>
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

  if (loading && !data) return <div className="skeleton h-20 w-full" />
  if (error) return <div className="text-loss text-sm py-4">Failed to load: {error}</div>
  const lamRaw = data?.lambda_lower ?? data?.aggregated_lambda
  if (!data || lamRaw == null) {
    return <div className="text-text-muted text-sm py-4">No tail dependence — need ≥2 positions</div>
  }

  const lam = parseFloat(lamRaw) || 0
  const threshold = parseFloat(data.threshold) || 0.3
  const color = lam < 0.2 ? 'text-profit' : lam < threshold ? 'text-warning' : 'text-loss'
  const label = lam < 0.2 ? 'LOW' : lam < threshold ? 'ELEVATED' : 'HIGH'
  const borderColor = lam < 0.2 ? 'border-l-profit' : lam < threshold ? 'border-l-warning' : 'border-l-loss'
  const perPos = Array.isArray(data.per_position) ? data.per_position : []

  return (
    <div className="space-y-3">
      <div className={`bg-bg-primary border border-border border-l-4 ${borderColor} p-3`}>
        <div className="text-[10px] uppercase text-text-muted">Aggregated λ (lower tail)</div>
        <div className="flex items-baseline gap-3">
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
          <div className="text-[10px] uppercase text-text-secondary">Per-Position</div>
          {perPos.slice(0, 8).map((p) => {
            const v = parseFloat(p.lambda) || 0
            const c = v < 0.2 ? 'text-profit' : v < 0.3 ? 'text-warning' : 'text-loss'
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

export default function PortfolioTab({ portfolio, trades, strategies, loading }) {
  return (
    <div className="space-y-2">
      <PortfolioSummary portfolio={portfolio} />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <Panel title="Equity Curve" className="md:col-span-1">
          <EquityChart />
        </Panel>

        <Panel title="Strategy Scores">
          <StrategiesPanel strategies={strategies} loading={loading.strategies} />
        </Panel>

        <Panel title="Open Positions">
          <PositionsTable positions={portfolio?.positions} loading={loading.portfolio} />
        </Panel>

        <Panel title="Strategy Positions">
          <StrategyPositions />
        </Panel>

        <Panel title="Recent Trades">
          <RecentTrades trades={trades} loading={loading.trades} />
        </Panel>
      </div>

      {/* risk / diversification panels */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        <Panel title="Correlation Heatmap" className="md:col-span-2">
          <CorrelationHeatmap />
        </Panel>
        <Panel title="Portfolio Tail Dependence">
          <TailDependenceCard />
        </Panel>
        <Panel title="Sector Concentration" className="md:col-span-3">
          <SectorConcentration />
        </Panel>
      </div>
    </div>
  )
}
