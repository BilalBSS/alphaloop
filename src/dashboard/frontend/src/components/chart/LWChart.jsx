import { useEffect, useMemo, useRef } from 'react'
import { useCandlestickChart } from './useCandlestickChart'
import { useChartData } from './useChartData'
import { useReplay } from './useReplay'
import { useCompare } from './useCompare'
import { useVolumeProfile } from './useVolumeProfile'
import PricePane from './panes/PricePane'
import IndicatorPane from './panes/IndicatorPane'
import MarkerPane from './panes/MarkerPane'
import ComparePane from './panes/ComparePane'
import VolumeProfilePane from './panes/VolumeProfilePane'
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
export default function LWChart({
  symbol,
  timeframe = '1Hour',
  days = 10,
  indicators = [],
  markerKinds = DEFAULT_MARKER_KINDS,
  replayEnabled = false,
  replayCutoff = null,
  compareAgainst = '',
  compareEnabled = false,
  volumeProfileEnabled = false,
}) {
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

  const { bars: liveBars, indicators: indicatorData, loading, error } = useChartData(symbol, timeframe, days, stableIndicators)
  const { markers: liveMarkers } = useMarkers(symbol, stableMarkerKinds)

  // / observation-mode replay: snapshot of bars + trades + signals + consensus knowable at time t
  // / zero re-simulation — pure read of existing db rows, dimmed chart only
  const { snapshot: replaySnapshot } = useReplay(symbol, replayCutoff, replayEnabled)

  // / pair normalized overlay — % change from first common timestamp for base + against
  // / disabled when replay is on so the two overlay modes never fight for the same price scale
  const compareActive = compareEnabled && !replayEnabled && !!compareAgainst
  const { snapshot: compareSnapshot } = useCompare(symbol, compareAgainst, timeframe, 90, compareActive)

  // / horizontal volume-at-price histogram — pulled from existing intraday rows
  const vpActive = volumeProfileEnabled && !replayEnabled
  const { profile: volumeProfile } = useVolumeProfile(symbol, 24, 30, timeframe, vpActive)

  // / project replay snapshot into the same shapes PricePane + MarkerPane already consume
  const replayBars = useMemo(() => {
    if (!replayEnabled || !replaySnapshot || !replaySnapshot.bars) return null
    const b = replaySnapshot.bars
    const n = Array.isArray(b.t) ? b.t.length : 0
    const candles = new Array(n)
    const volume = new Array(n)
    let w = 0
    for (let i = 0; i < n; i++) {
      const t = Math.floor(new Date(b.t[i]).getTime() / 1000)
      const o = parseFloat(b.o[i])
      const h = parseFloat(b.h[i])
      const l = parseFloat(b.l[i])
      const c = parseFloat(b.c[i])
      const v = parseFloat(b.v[i] || 0)
      if (!Number.isFinite(t) || ![o, h, l, c].every(Number.isFinite)) continue
      candles[w] = { time: t, open: o, high: h, low: l, close: c }
      volume[w] = {
        time: t,
        value: v,
        color: c >= o ? 'rgba(0, 220, 130, 0.25)' : 'rgba(255, 71, 87, 0.25)',
      }
      w++
    }
    candles.length = w
    volume.length = w
    // / lightweight-charts requires ascending unique times — replay bars already sorted, but guard anyway
    candles.sort((a, b) => a.time - b.time)
    volume.sort((a, b) => a.time - b.time)
    return { candles, volume }
  }, [replayEnabled, replaySnapshot])

  const replayMarkers = useMemo(() => {
    if (!replayEnabled || !replaySnapshot) return null
    return {
      trades: Array.isArray(replaySnapshot.trades) ? replaySnapshot.trades : [],
      signals: Array.isArray(replaySnapshot.signals) ? replaySnapshot.signals : [],
      insiders: [],
      earnings: [],
      regime: [],
      consensus: Array.isArray(replaySnapshot.consensus) ? replaySnapshot.consensus : [],
    }
  }, [replayEnabled, replaySnapshot])

  // / active data sources: replay overrides live when enabled AND snapshot is ready
  const bars = (replayEnabled && replayBars) ? replayBars : liveBars
  const markers = (replayEnabled && replayMarkers) ? replayMarkers : liveMarkers

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
      {replayEnabled && (
        <div className="absolute top-1 left-2 z-10 px-1.5 py-0.5 text-[10px] font-mono uppercase font-semibold text-loss border border-loss bg-card/80 pointer-events-none">
          REPLAY @ {replayCutoff || '--'}
        </div>
      )}
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
      {isReady && <ComparePane
        chart={chart}
        snapshot={compareSnapshot}
        visible={compareActive}
      />}
      {isReady && <VolumeProfilePane
        profile={volumeProfile}
        visible={vpActive}
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
