import { useEffect, useRef } from 'react'
import { createSeriesMarkers } from 'lightweight-charts'
import { buildTradeMarkers } from '../markers/tradeMarkers'
import { buildSignalMarkers } from '../markers/signalMarkers'
import { buildInsiderMarkers } from '../markers/insiderMarkers'
import { buildEarningsMarkers } from '../markers/earningsMarkers'
import { buildRegimeMarkers } from '../markers/regimeBands'
import { createConsensusStrip } from '../markers/consensusStrip'

// / pure imperative writer that pushes trade + signal + insider + earnings + regime markers
// / into the price series via lightweight-charts v5 createSeriesMarkers plugin
// / consensus strip is a histogram sibling series owned by this pane and torn down on unmount
// / kinds prop is the filter subset the user wants visible — absent kinds are not merged
export default function MarkerPane({ chart, priceSeries, candles, markers, kinds }) {
  const markersHandleRef = useRef(null)
  const consensusRef = useRef(null)

  // / initialize the marker plugin once per (chart, priceSeries) pair
  // / v5 moved markers from series.setMarkers to the createSeriesMarkers external plugin
  useEffect(() => {
    if (!chart || !priceSeries) return undefined
    let handle
    try {
      handle = createSeriesMarkers(priceSeries, [])
    } catch {
      // / fallback for v5 minor versions that kept legacy series.setMarkers
      handle = {
        setMarkers: (m) => {
          try { priceSeries.setMarkers(m) } catch { /* / api gone */ }
        },
        detach: () => {
          try { priceSeries.setMarkers([]) } catch { /* / api gone */ }
        },
      }
    }
    markersHandleRef.current = handle
    return () => {
      const h = markersHandleRef.current
      markersHandleRef.current = null
      if (!h) return
      try {
        if (typeof h.detach === 'function') h.detach()
        else if (typeof h.setMarkers === 'function') h.setMarkers([])
      } catch { /* / already disposed */ }
    }
  }, [chart, priceSeries])

  // / rebuild marker array whenever markers data, candles, or kinds change
  useEffect(() => {
    const handle = markersHandleRef.current
    if (!handle) return undefined
    if (!Array.isArray(candles)) return undefined
    const active = new Set(Array.isArray(kinds) ? kinds : [])
    const safe = markers || {}
    const merged = []
    if (active.has('trades')) merged.push(...buildTradeMarkers(safe.trades, candles))
    if (active.has('signals')) merged.push(...buildSignalMarkers(safe.signals, candles))
    if (active.has('insiders')) merged.push(...buildInsiderMarkers(safe.insiders, candles))
    if (active.has('earnings')) merged.push(...buildEarningsMarkers(safe.earnings, candles))
    if (active.has('regime')) merged.push(...buildRegimeMarkers(safe.regime, candles))
    // / lightweight-charts requires markers sorted by time ascending
    merged.sort((a, b) => a.time - b.time)
    try {
      handle.setMarkers(merged)
    } catch { /* / chart gone */ }
    return undefined
  }, [candles, markers, kinds])

  // / consensus strip: create/destroy alongside the price pane, update on data change
  useEffect(() => {
    if (!chart) return undefined
    const active = new Set(Array.isArray(kinds) ? kinds : [])
    if (!active.has('consensus')) {
      // / consensus disabled — dispose any prior strip
      if (consensusRef.current) {
        try { consensusRef.current.destroy() } catch { /* / disposed */ }
        consensusRef.current = null
      }
      return undefined
    }
    if (!consensusRef.current) {
      try {
        consensusRef.current = createConsensusStrip(chart, 0)
      } catch {
        consensusRef.current = null
      }
    }
    const strip = consensusRef.current
    if (strip && typeof strip.update === 'function') {
      try { strip.update((markers && markers.consensus) || [], candles || []) }
      catch { /* / bad payload */ }
    }
    return undefined
  }, [chart, candles, markers, kinds])

  // / full teardown on unmount
  useEffect(() => {
    return () => {
      if (consensusRef.current) {
        try { consensusRef.current.destroy() } catch { /* / disposed */ }
        consensusRef.current = null
      }
    }
  }, [])

  return null
}
