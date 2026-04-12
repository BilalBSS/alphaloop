import { useEffect } from 'react'

// / thin wrapper that writes candle + volume data into pre-created lightweight-charts series
// / step 3: candles + volume only (no indicators, no markers)
// / the parent LWChart owns the chart + series; this component only pushes data and fits content
export default function PricePane({ chart, priceSeries, volumeSeries, candles, volume }) {
  useEffect(() => {
    if (!priceSeries || !candles) return
    priceSeries.setData(candles)
    if (volumeSeries && volume) volumeSeries.setData(volume)
    if (chart && candles.length > 0) chart.timeScale().fitContent()
  }, [chart, priceSeries, volumeSeries, candles, volume])

  return null
}
