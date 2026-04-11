import { useMemo, useRef } from 'react'
import { useCandlestickChart } from './useCandlestickChart'
import { useChartData } from './useChartData'
import PricePane from './panes/PricePane'
import IndicatorPane from './panes/IndicatorPane'
import { countOscillatorPanes } from './indicatorRegistry'
import { darkTheme } from './constants/theme'

// / tradingview lightweight-charts replacement for the legacy recharts IntradayChart
// / step 3 scope: candles + volume histogram only — no indicators (step 4), no markers (step 5)
// / step 4: adds indicator rendering via indicatorRegistry + IndicatorPane
// / manual test path:
// /   1. set window.__USE_LWC_CHART = true or localStorage USE_LWC_CHART = 'true' (default on)
// /   2. open a symbol in the analysis tab, switch timeframe toggle to '2h'
// /   3. verify green/red candles render, volume bars sit at bottom strip, timescale auto-fits
// /   4. resize browser window -> chart should reflow to container width
// /   5. switch symbols -> old data should clear and new data should render
export default function LWChart({ symbol, timeframe = '1Hour', days = 10, indicators = [] }) {
  const containerRef = useRef(null)
  const { chart, priceSeries, volumeSeries, isReady } = useCandlestickChart(containerRef, {
    theme: darkTheme,
    showVolume: true,
  })

  // / stabilize indicators reference across parent re-renders so IndicatorPane's effect
  // / does not fire when the set is unchanged (picker clicks stay snappy, no unnecessary diff work)
  const indicatorsKey = Array.isArray(indicators) ? indicators.slice().sort().join(',') : ''
  const stableIndicators = useMemo(
    () => (indicatorsKey ? indicatorsKey.split(',') : []),
    [indicatorsKey],
  )

  const { bars, indicators: indicatorData, loading, error } = useChartData(symbol, timeframe, days, stableIndicators)

  const hasData = bars.candles.length > 0
  const showLoading = loading && !hasData
  const showEmpty = !loading && !hasData && !error

  // / container height grows with oscillator pane count so v5 auto-pane layout has room
  // / 260 base for price pane + 80 per active oscillator pane
  const numOscPanes = useMemo(() => countOscillatorPanes(stableIndicators), [stableIndicators])
  const containerHeight = 260 + 80 * numOscPanes

  return (
    <div className="relative" style={{ height: containerHeight }}>
      <div ref={containerRef} className="absolute inset-0" />
      {isReady && <PricePane
        chart={chart}
        priceSeries={priceSeries}
        volumeSeries={volumeSeries}
        candles={bars.candles}
        volume={bars.volume}
      />}
      {isReady && <IndicatorPane
        chart={chart}
        priceSeries={priceSeries}
        candles={bars.candles}
        indicators={stableIndicators}
        indicatorData={indicatorData}
      />}
      {showLoading && (
        <div className="absolute inset-0 flex items-center justify-center text-text-muted text-sm pointer-events-none">
          Loading intraday...
        </div>
      )}
      {showEmpty && (
        <div className="absolute inset-0 flex items-center justify-center text-text-muted text-sm pointer-events-none">
          No intraday data yet
        </div>
      )}
      {error && !hasData && (
        <div className="absolute inset-0 flex items-center justify-center text-loss text-sm pointer-events-none">
          Chart error: {error}
        </div>
      )}
    </div>
  )
}
