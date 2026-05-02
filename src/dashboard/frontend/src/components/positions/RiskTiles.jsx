import { useMemo } from 'react'

// / cash · orders · stops

function fmtUsd(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—'
  const v = Number(n)
  return `${v < 0 ? '−$' : '$'}${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

function fmtUsdShort(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—'
  const v = Math.abs(Number(n))
  if (v >= 1000) return `${Number(n) < 0 ? '−$' : '$'}${(v / 1000).toFixed(1)}K`
  return fmtUsd(n, 0)
}

export default function RiskTiles({ portfolio }) {
  const cash = portfolio?.cash
  const buyingPower = portfolio?.buying_power
  const equity = portfolio?.equity ?? portfolio?.nav ?? portfolio?.total_value
  const cashPct = cash != null && equity > 0 ? (Number(cash) / Number(equity)) * 100 : null

  const stops = useMemo(() => {
    const positions = portfolio?.positions
    if (!Array.isArray(positions)) return null
    return positions.filter((p) => p.stop_loss != null || p.stop_price != null).length
  }, [portfolio])

  const restingOrders = portfolio?.resting_orders ?? portfolio?.open_orders ?? null

  return (
    <div className="grid c3" style={{ marginTop: 18 }}>
      <div className="tile">
        <div className="lab">cash</div>
        <div className="v">{fmtUsd(cash)}</div>
        <div className="x">
          {cashPct != null ? `${cashPct.toFixed(1)}% NAV` : '— NAV'}
          {buyingPower != null && <> · buying power <b>{fmtUsdShort(buyingPower)}</b></>}
        </div>
      </div>

      <div className="tile">
        <div className="lab">resting orders</div>
        <div className="v">{restingOrders != null ? restingOrders : '—'}</div>
        <div className="x">{restingOrders != null ? 'GTC default' : 'broker order book unsynced'}</div>
      </div>

      <div className="tile">
        <div className="lab">stops armed</div>
        <div className="v">{stops != null ? stops : '—'}</div>
        <div className="x">{stops != null && stops > 0 ? 'ATR-trail · per-strategy config' : 'set via strategy stop_loss'}</div>
      </div>
    </div>
  )
}
