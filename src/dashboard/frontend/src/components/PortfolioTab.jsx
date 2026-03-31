import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import Panel from './Panel'
import { SkeletonTable, SkeletonChart } from './Skeleton'
import { useApi } from '../hooks/useApi'

function EquityChart() {
  const { data, loading } = useApi('/api/equity-history?period=1D&timeframe=5Min', 60000)

  if (loading && !data) return <SkeletonChart />
  if (!data || !data.timestamps || data.timestamps.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-text-muted text-sm">
        Equity history loading — updates every minute
      </div>
    )
  }

  const chartData = data.timestamps.map((ts, i) => ({
    time: new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    equity: data.equity[i],
  }))

  const minEq = Math.min(...chartData.map(d => d.equity))
  const maxEq = Math.max(...chartData.map(d => d.equity))
  const isUp = chartData.length > 1 && chartData[chartData.length - 1].equity >= chartData[0].equity

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={chartData}>
        <defs>
          <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={isUp ? '#00dc82' : '#ff4757'} stopOpacity={0.15} />
            <stop offset="100%" stopColor={isUp ? '#00dc82' : '#ff4757'} stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="time" tick={{ fontSize: 9, fill: '#8888a0' }} interval="preserveStartEnd" />
        <YAxis domain={[minEq * 0.999, maxEq * 1.001]} hide />
        <Tooltip
          contentStyle={{ background: '#12121a', border: '1px solid #1e1e2a', fontSize: 12 }}
          labelStyle={{ color: '#8888a0' }}
          formatter={(v) => [`$${v.toLocaleString(undefined, { minimumFractionDigits: 2 })}`, 'Equity']}
        />
        <Area type="monotone" dataKey="equity" stroke={isUp ? '#00dc82' : '#ff4757'} strokeWidth={2}
          fill="url(#eqGrad)" />
      </AreaChart>
    </ResponsiveContainer>
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
          {pnl >= 0 ? '+' : ''}${pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          <span className="text-xs ml-1">({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)</span>
        </div>
      </div>
      <div className="bg-bg-primary border border-border p-2">
        <div className="text-[10px] uppercase text-text-muted">Cash</div>
        <div className="text-lg font-mono font-bold text-text-primary">
          ${portfolio.cash?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) || '--'}
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
                {pl >= 0 ? '+' : ''}${pl.toFixed(2)}
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
          const pnl = parseFloat(t.pnl || 0)
          return (
            <tr key={i} className="hover:bg-bg-hover border-t border-border" style={{ height: 36 }}>
              <td className="px-2 py-1 font-mono font-semibold">{t.symbol}</td>
              <td className={`px-2 py-1 ${t.side === 'buy' ? 'text-profit' : 'text-loss'}`}>
                {t.side?.toUpperCase()}
              </td>
              <td className={`px-2 py-1 text-right font-mono ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
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

function StrategiesPanel({ strategies, loading }) {
  if (loading) return <SkeletonTable rows={4} cols={3} />
  if (!strategies || strategies.length === 0) {
    return <div className="text-text-muted text-sm py-4">No strategies loaded</div>
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-text-secondary text-[11px] uppercase">
          <th className="text-left px-2 py-1">Strategy</th>
          <th className="text-right px-2 py-1">Score</th>
          <th className="text-right px-2 py-1">W/L</th>
        </tr>
      </thead>
      <tbody>
        {strategies.slice(0, 8).map((s, i) => (
          <tr key={i} className="hover:bg-bg-hover border-t border-border" style={{ height: 36 }}>
            <td className="px-2 py-1 truncate max-w-[120px]">{s.strategy_id}</td>
            <td className="px-2 py-1 text-right font-mono">
              {parseFloat(s.composite_score || 0).toFixed(2)}
            </td>
            <td className="px-2 py-1 text-right font-mono text-text-secondary">
              {(parseFloat(s.win_rate || 0) * 100).toFixed(0)}%
            </td>
          </tr>
        ))}
      </tbody>
    </table>
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

        <Panel title="Recent Trades">
          <RecentTrades trades={trades} loading={loading.trades} />
        </Panel>
      </div>
    </div>
  )
}
