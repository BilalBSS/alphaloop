// / pure function: insiders[] + candles[] -> lightweight-charts seriesMarker[]
// / lightweight-charts v5 only exposes circle/square/arrowUp/arrowDown — no diamond shape
// / render clusters as squares and singletons as small circles; color by buy/sell
import { snapToCandle } from './snap'

const COLOR_BUY = '#40c057'
const COLOR_SELL = '#fa5252'

export function buildInsiderMarkers(insiders, candles) {
  if (!Array.isArray(insiders) || insiders.length === 0) return []
  if (!Array.isArray(candles) || candles.length === 0) return []
  const out = []
  for (const ins of insiders) {
    if (!ins || !ins.time) continue
    const snapped = snapToCandle(ins.time, candles)
    if (snapped === null) continue
    const isBuy = ins.transaction_type === 'buy'
    const size = Number(ins.cluster_size) || 1
    const isCluster = size >= 3
    out.push({
      time: snapped,
      position: isBuy ? 'belowBar' : 'aboveBar',
      color: isBuy ? COLOR_BUY : COLOR_SELL,
      shape: isCluster ? 'square' : 'circle',
      text: isCluster ? `I${size}` : 'I',
    })
  }
  return out
}
