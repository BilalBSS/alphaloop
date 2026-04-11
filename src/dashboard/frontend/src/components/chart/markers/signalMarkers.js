// / pure function: signals[] + candles[] -> lightweight-charts seriesMarker[]
// / small circle markers, colored by action, positioned inBar to distinguish from trade arrows
import { snapToCandle } from './snap'

const COLOR_BUY = 'rgba(0, 220, 130, 0.85)'
const COLOR_SELL = 'rgba(255, 71, 87, 0.85)'

export function buildSignalMarkers(signals, candles) {
  if (!Array.isArray(signals) || signals.length === 0) return []
  if (!Array.isArray(candles) || candles.length === 0) return []
  const out = []
  for (const s of signals) {
    if (!s || !s.time) continue
    const snapped = snapToCandle(s.time, candles)
    if (snapped === null) continue
    const isBuy = s.action === 'buy'
    out.push({
      time: snapped,
      position: 'inBar',
      color: isBuy ? COLOR_BUY : COLOR_SELL,
      shape: 'circle',
      text: '',
    })
  }
  return out
}
