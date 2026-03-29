import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import Panel from './Panel'
import { SkeletonTable, SkeletonChart } from './Skeleton'

function EquityChart({ trades }) {
  if (!trades || trades.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-text-muted text-sm">
        No trades yet — waiting for first signal
      </div>
    )
  }
  // build simple equity curve from trade P&L
  let equity = 100000
  const data = trades.slice().reverse().map((t, i) => {
    const pnl = parseFloat(t.pnl || 0)
    equity += pnl
    return { idx: i, equity: Math.round(equity * 100) / 100 }
  })

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data}>
        <defs>
          <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.15} />
            <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="idx" hide />
        <YAxis domain={['auto', 'auto']} hide />
        <Tooltip
          contentStyle={{ background: '#12121a', border: '1px solid #1e1e2a', fontSize: 12 }}
          labelStyle={{ color: '#8888a0' }}
          formatter={(v) => [`$${v.toLocaleString()}`, 'Equity']}
        />
        <Area type="monotone" dataKey="equity" stroke="#3b82f6" strokeWidth={2}
          fill="url(#eqGrad)" />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function PositionsTable({ positions, loading }) {
  if (loading) return <SkeletonTable rows={3} cols={4} />
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
        </tr>
      </thead>
      <tbody>
        {positions.map((p, i) => (
          <tr key={i} className="hover:bg-bg-hover border-t border-border" style={{ height: 36 }}>
            <td className="px-2 py-1 font-mono font-semibold">{p.symbol}</td>
            <td className={`px-2 py-1 ${p.side === 'buy' ? 'text-profit' : 'text-loss'}`}>
              {p.side?.toUpperCase()}
            </td>
            <td className="px-2 py-1 text-right font-mono">{p.qty}</td>
            <td className="px-2 py-1 text-right font-mono">
              ${parseFloat(p.entry_price || p.price || 0).toFixed(2)}
            </td>
          </tr>
        ))}
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
                {t.created_at?.split('T')[0] || '--'}
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
    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
      <Panel title="Equity Curve" className="md:col-span-1">
        {loading.trades ? <SkeletonChart /> : <EquityChart trades={trades} />}
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
  )
}
