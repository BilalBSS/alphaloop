import { useEffect, useMemo, useRef } from 'react'
import { useCandlestickChart } from './useCandlestickChart'
import { useChartData } from './useChartData'
import PricePane from './panes/PricePane'
import IndicatorPane from './panes/IndicatorPane'
import MarkerPane from './panes/MarkerPane'
import { useMarkers } from './markers/useMarkers'
import { useDrawings, useChartTools } from './useDrawings'
import { countOscillatorPanes } from './indicatorRegistry'
import { darkTheme } from './constants/theme'

// / default marker kinds shown when no chart_state override exists — exported so SymbolDetail
// / can share the single source of truth and avoid drift
export const DEFAULT_MARKER_KINDS = ['trades', 'signals', 'earnings']

// / tradingview lightweight-charts replacement for the legacy recharts IntradayChart
// / step 3 scope: candles + volume histogram only — no indicators (step 4), no markers (step 5)
// / step 4: adds indicator rendering via indicatorRegistry + IndicatorPane
// / step 5: adds markers (trades, signals, insiders, earnings, regime, consensus) via MarkerPane
// / manual test path:
// /   1. set window.__USE_LWC_CHART = true or localStorage USE_LWC_CHART = 'true' (default on)
// /   2. open a symbol in the analysis tab, switch timeframe toggle to '2h'
// /   3. verify green/red candles render, volume bars sit at bottom strip, timescale auto-fits
// /   4. resize browser window -> chart should reflow to container width
// /   5. switch symbols -> old data should clear and new data should render
export default function LWChart({ symbol, timeframe = '1Hour', days = 10, indicators = [], markerKinds = DEFAULT_MARKER_KINDS }) {
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

  // / same stability guarantee for markerKinds so useMarkers + MarkerPane don't refetch/redraw on every render
  const markerKindsKey = Array.isArray(markerKinds) ? markerKinds.slice().sort().join(',') : ''
  const stableMarkerKinds = useMemo(
    () => (markerKindsKey ? markerKindsKey.split(',') : []),
    [markerKindsKey],
  )

  const { bars, indicators: indicatorData, loading, error } = useChartData(symbol, timeframe, days, stableIndicators)
  const { markers } = useMarkers(symbol, stableMarkerKinds)

  // / drawings are gated on isReady so the DrawingManager only instantiates after chart + series exist
  // / library load is lazy inside the hook — a missing import degrades to a no-op shape
  const drawingsHook = useDrawings(isReady ? chart : null, isReady ? priceSeries : null, symbol)
  const chartTools = useChartTools()
  // / pin chartTools' stable methods to a ref so the publish effect doesn't list chartTools in deps
  // / and re-fire when the provider re-renders with a new value object after publish
  const chartToolsRef = useRef(null)
  useEffect(() => {
    chartToolsRef.current = chartTools
  }, [chartTools])
  // / publish live controls into the context so DrawingToolbar (sibling under the same provider)
  // / can drive setTool/clear/undo against the currently mounted manager
  useEffect(() => {
    const ct = chartToolsRef.current
    if (!ct || typeof ct.publish !== 'function') return undefined
    ct.publish({
      setTool: drawingsHook.setTool,
      clear: drawingsHook.clear,
      undo: drawingsHook.undo,
    })
    return () => {
      const ct2 = chartToolsRef.current
      if (ct2 && typeof ct2.publish === 'function') ct2.publish(null)
    }
  }, [drawingsHook.setTool, drawingsHook.clear, drawingsHook.undo])
  // / keep context's activeTool mirror synced so the toolbar highlights the active button
  useEffect(() => {
    const ct = chartToolsRef.current
    if (!ct || typeof ct.syncActive !== 'function') return
    ct.syncActive(drawingsHook.activeTool)
  }, [drawingsHook.activeTool])

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
      {isReady && <MarkerPane
        chart={chart}
        priceSeries={priceSeries}
        candles={bars.candles}
        markers={markers}
        kinds={stableMarkerKinds}
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
