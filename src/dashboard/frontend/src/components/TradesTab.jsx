import Panel from './Panel'
import { SkeletonTable } from './Skeleton'

export default function TradesTab({ trades, loading }) {
  if (loading) {
    return <Panel title="Trade Log"><SkeletonTable rows={8} cols={6} /></Panel>
  }

  return (
    <Panel title="Trade Log">
      {!trades || trades.length === 0 ? (
        <div className="text-text-muted text-sm py-8">No trades recorded yet</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-secondary text-[11px] uppercase">
                <th className="text-left px-2 py-1 sticky left-0 bg-bg-surface">Symbol</th>
                <th className="text-left px-2 py-1">Side</th>
                <th className="text-right px-2 py-1">Qty</th>
                <th className="text-right px-2 py-1">Price</th>
                <th className="text-right px-2 py-1">P&L</th>
                <th className="text-left px-2 py-1">Strategy</th>
                <th className="text-right px-2 py-1">Date</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => {
                const pnl = parseFloat(t.pnl || 0)
                return (
                  <tr key={i} className="hover:bg-bg-hover border-t border-border" style={{ height: 36 }}>
                    <td className="px-2 py-1 font-mono font-semibold sticky left-0 bg-bg-surface">{t.symbol}</td>
                    <td className={`px-2 py-1 ${t.side === 'buy' ? 'text-profit' : 'text-loss'}`}>
                      {t.side?.toUpperCase()}
                    </td>
                    <td className="px-2 py-1 text-right font-mono">{(() => { const q = parseFloat(t.qty || 0); return q < 1 ? q.toPrecision(4) : q % 1 === 0 ? q.toFixed(0) : q.toFixed(2) })()}</td>
                    <td className="px-2 py-1 text-right font-mono">
                      ${parseFloat(t.price || 0).toFixed(2)}
                    </td>
                    <td className={`px-2 py-1 text-right font-mono ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {pnl !== 0 ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}` : '--'}
                    </td>
                    <td className="px-2 py-1 text-text-secondary truncate max-w-[100px]">{t.strategy_id || '--'}</td>
                    <td className="px-2 py-1 text-right text-text-muted whitespace-nowrap">{t.created_at?.replace('T', ' ').slice(0, 16) || '--'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
