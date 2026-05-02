import { HistogramSeries } from 'lightweight-charts'
import { snapToCandle } from './snap'

// / consensus strip: a tiny histogram floating at the bottom of the price pane
// / bullish = +1 (green), bearish = -1 (red), neutral = 0 (gray), disagree = 0 (orange)
// / returns a handle { series, update(consensusSeries, candles), destroy() } owned by MarkerPane

const CONSENSUS_VALUES = {
  bullish: 1,
  bearish: -1,
  neutral: 0,
  disagree: 0,
}

const CONSENSUS_COLORS = {
  bullish: 'rgba(127, 184, 122, 0.85)',
  bearish: 'rgba(213, 106, 91, 0.85)',
  neutral: 'rgba(184, 179, 163, 0.55)',
  disagree: 'rgba(216, 180, 102, 0.85)',
}

export function createConsensusStrip(chart, paneIndex = 0) {
  // / dedicated priceScaleId isolates scale so the strip hugs the bottom without interfering with price axis
  const series = chart.addSeries(HistogramSeries, {
    priceFormat: { type: 'volume' },
    priceScaleId: 'consensus',
    priceLineVisible: false,
    lastValueVisible: false,
    title: '',
  }, paneIndex)
  try {
    series.priceScale().applyOptions({
      scaleMargins: { top: 0.95, bottom: 0 },
    })
  } catch {
    // / lightweight-charts may reject custom scale ids in some versions; swallow
  }
  const apply = (consensusSeries, candles) => {
    if (!Array.isArray(consensusSeries) || !Array.isArray(candles) || candles.length === 0) {
      try { series.setData([]) } catch { /* / disposed */ }
      return
    }
    const out = []
    for (const c of consensusSeries) {
      if (!c || !c.time) continue
      const snapped = snapToCandle(c.time, candles)
      if (snapped === null) continue
      const key = String(c.consensus || '').toLowerCase()
      if (!(key in CONSENSUS_VALUES)) continue
      out.push({
        time: snapped,
        value: CONSENSUS_VALUES[key],
        color: CONSENSUS_COLORS[key],
      })
    }
    // / de-dupe + sort ascending (lightweight-charts requires strict ordering)
    const byTime = new Map()
    for (const p of out) byTime.set(p.time, p)
    const sorted = Array.from(byTime.values()).sort((a, b) => a.time - b.time)
    try { series.setData(sorted) } catch { /* / disposed */ }
  }
  return {
    series,
    update: apply,
    destroy: () => {
      try { chart.removeSeries(series) } catch { /* / already disposed */ }
    },
  }
}
