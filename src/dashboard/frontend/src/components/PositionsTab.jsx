import { Fragment, useMemo, useState } from 'react'
import SectionH from './ui/SectionH'
import Card from './ui/Card'
import Pill from './ui/Pill'
import { useApiLive } from '../hooks/useApiLive'
import PositionsTable from './positions/PositionsTable'
import SectorMix from './positions/SectorMix'
import CorrelationHeatmap from './positions/CorrelationHeatmap'
import RiskTiles from './positions/RiskTiles'
import TailDependenceCard from './positions/TailDependenceCard'
import { clickableProps } from './ui/clickable'

// / strategy attribution rows

function groupBySymbol(positions) {
  if (!Array.isArray(positions)) return []
  const bySymbol = new Map()
  for (const p of positions) {
    const sym = p.symbol
    if (!bySymbol.has(sym)) bySymbol.set(sym, [])
    bySymbol.get(sym).push(p)
  }
  const rows = []
  for (const [, entries] of bySymbol) {
    const tracked = entries.filter((e) => e.strategy_id && e.strategy_id.toLowerCase() !== 'untracked')
    const untracked = entries.filter((e) => !e.strategy_id || e.strategy_id.toLowerCase() === 'untracked')
    if (tracked.length > 0) {
      rows.push({ ...tracked[0], extras: [...tracked.slice(1), ...untracked] })
    } else if (untracked.length > 0) {
      rows.push({ ...untracked[0], extras: untracked.slice(1) })
    }
  }
  return rows
}

function StrategyAttribution() {
  const { data, loading } = useApiLive('/api/strategy-positions', 30000, ['position_update', 'trade_executed'])
  const [expanded, setExpanded] = useState({})
  const rows = useMemo(() => groupBySymbol(data), [data])

  return (
    <Card title={<><b>strategy attribution</b></>} meta="symbol → owning strategy" p0>
      {loading && rows.length === 0 ? (
        <div className="empty-state"><div className="empty-state-title">loading attribution</div></div>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">no strategy attribution</div>
          <div className="empty-state-hint">populated as strategies open tracked positions.</div>
        </div>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th style={{ width: 24 }}></th>
              <th>symbol</th>
              <th>strategy</th>
              <th className="r">qty</th>
              <th className="r">avg entry</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const key = `${r.symbol}-${r.strategy_id || 'untracked'}-${i}`
              const hasExtras = r.extras && r.extras.length > 0
              const open = !!expanded[key]
              return (
                <Fragment key={key}>
                  <tr
                    className={hasExtras ? 'click' : ''}
                    {...(hasExtras ? clickableProps(() => setExpanded({ ...expanded, [key]: !open })) : {})}
                  >
                    <td className="dim tiny">{hasExtras ? (open ? '▾' : '▸') : ''}</td>
                    <td className="sym">{r.symbol}</td>
                    <td className="dim">
                      {r.strategy_id || 'untracked'}
                      {hasExtras && (
                        <span style={{ marginLeft: 8 }}>
                          <Pill variant="strat">+{r.extras.length}</Pill>
                        </span>
                      )}
                    </td>
                    <td className="r">{r.qty}</td>
                    <td className="r">${parseFloat(r.avg_entry_price ?? 0).toFixed(2)}</td>
                  </tr>
                  {hasExtras && open && r.extras.map((ex, j) => (
                    <tr key={`${key}-x-${j}`}>
                      <td></td>
                      <td className="dim tiny">↳</td>
                      <td className="dim">{ex.strategy_id || 'untracked'}</td>
                      <td className="r dim">{ex.qty}</td>
                      <td className="r dim">${parseFloat(ex.avg_entry_price ?? 0).toFixed(2)}</td>
                    </tr>
                  ))}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      )}
    </Card>
  )
}

export default function PositionsTab({ portfolio }) {
  const positionsCount = portfolio?.positions_count ?? portfolio?.positions?.length ?? 0
  return (
    <SectionH
      num="02"
      title="positions"
      em="live book"
      meta={<><code>/api/portfolio</code> · {positionsCount} open</>}
    >
      <PositionsTable portfolio={portfolio} />

      <StrategyAttribution />

      <div className="grid c2">
        <SectorMix />
        <CorrelationHeatmap />
      </div>

      <div style={{ marginTop: 18 }}>
        <TailDependenceCard />
      </div>

      <RiskTiles portfolio={portfolio} />
    </SectionH>
  )
}
