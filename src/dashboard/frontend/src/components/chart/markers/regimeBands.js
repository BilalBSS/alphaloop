// / regime band rendering — lightweight-charts does not natively support background time shading
// / v1 limitation: we approximate bands by emitting a single seriesMarker at each regime transition
// / the marker text = regime name, color = regime tint, positioned aboveBar so bands read as labels
// / full background shading would require a custom series plugin — deferred to v2
import { snapToCandle } from './snap'

const REGIME_COLORS = {
  bull: '#00dc82',
  bear: '#ff4757',
  sideways: '#8888a0',
  high_vol: '#ffa94d',
}

const REGIME_LABELS = {
  bull: 'BULL',
  bear: 'BEAR',
  sideways: 'SIDE',
  high_vol: 'VOL',
}

export function buildRegimeMarkers(bands, candles) {
  if (!Array.isArray(bands) || bands.length === 0) return []
  if (!Array.isArray(candles) || candles.length === 0) return []
  const out = []
  for (const b of bands) {
    if (!b || !b.start || !b.regime) continue
    const snapped = snapToCandle(b.start, candles)
    if (snapped === null) continue
    const key = String(b.regime).toLowerCase()
    const color = REGIME_COLORS[key] || '#8888a0'
    const label = REGIME_LABELS[key] || key.toUpperCase()
    out.push({
      time: snapped,
      position: 'aboveBar',
      color,
      shape: 'circle',
      text: label,
    })
  }
  return out
}
