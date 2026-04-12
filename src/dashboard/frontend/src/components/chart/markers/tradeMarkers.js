// / pure function: trades[] + candles[] -> lightweight-charts seriesMarker[]
// / buy = arrowUp below bar (green), sell = arrowDown above bar (red)
// / snaps each marker to the nearest candle time so markers land on the axis grid
import { snapToCandle } from './snap'

const COLOR_BUY = '#00dc82'
const COLOR_SELL = '#ff4757'

export function buildTradeMarkers(trades, candles) {
  if (!Array.isArray(trades) || trades.length === 0) return []
  if (!Array.isArray(candles) || candles.length === 0) return []
  const out = []
  for (const t of trades) {
    if (!t || !t.time) continue
    const snapped = snapToCandle(t.time, candles)
    if (snapped === null) continue
    const isBuy = t.side === 'buy'
    const pnlText = typeof t.pnl === 'number' && Number.isFinite(t.pnl) ? ` ${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}` : ''
    out.push({
      time: snapped,
      position: isBuy ? 'belowBar' : 'aboveBar',
      color: isBuy ? COLOR_BUY : COLOR_SELL,
      shape: isBuy ? 'arrowUp' : 'arrowDown',
      text: `${isBuy ? 'B' : 'S'}${pnlText}`,
    })
  }
  return out
}
