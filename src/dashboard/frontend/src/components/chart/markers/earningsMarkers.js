// / pure function: earnings[] + candles[] -> lightweight-charts seriesMarker[]
// / text "E" above bar, color by beat/miss/inline classification
import { snapToCandle } from './snap'

const COLOR_BEAT = '#00dc82'
const COLOR_MISS = '#ff4757'
const COLOR_INLINE = '#8888a0'

export function buildEarningsMarkers(earnings, candles) {
  if (!Array.isArray(earnings) || earnings.length === 0) return []
  if (!Array.isArray(candles) || candles.length === 0) return []
  const out = []
  for (const e of earnings) {
    if (!e || !e.time) continue
    const snapped = snapToCandle(e.time, candles)
    if (snapped === null) continue
    let color = COLOR_INLINE
    if (e.type === 'beat') color = COLOR_BEAT
    else if (e.type === 'miss') color = COLOR_MISS
    out.push({
      time: snapped,
      position: 'aboveBar',
      color,
      shape: 'square',
      text: 'E',
    })
  }
  return out
}
