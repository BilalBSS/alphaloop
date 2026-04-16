import { useEffect, useState } from 'react'
import { createChart, CandlestickSeries, HistogramSeries } from 'lightweight-charts'
import { chartLayoutOptions } from './constants/theme'

// / owns the chart lifecycle: creates once, cleans up once
// / returns a tuple { chart, priceSeries, volumeSeries, isReady } that only re-renders when ready
// / react 19 strictmode double-mount safe: cleanup calls chart.remove() and the next mount creates fresh
// / gh issue #1920 workaround: never hold chart refs outside the useEffect, cleanup is idempotent
export function useCandlestickChart(containerRef, { theme, showVolume = true }) {
  const [state, setState] = useState({ chart: null, priceSeries: null, volumeSeries: null, isReady: false })

  useEffect(() => {
    const container = containerRef.current
    if (!container) return undefined

    const chart = createChart(container, {
      width: container.clientWidth || 600,
      height: container.clientHeight || 260,
      ...chartLayoutOptions(theme),
    })

    const priceSeries = chart.addSeries(CandlestickSeries, {
      upColor: theme.up,
      downColor: theme.down,
      borderUpColor: theme.up,
      borderDownColor: theme.down,
      wickUpColor: theme.up,
      wickDownColor: theme.down,
    })
    // / keep room for volume overlay at the bottom 20%
    priceSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.05, bottom: showVolume ? 0.22 : 0.05 },
    })

    let volumeSeries = null
    if (showVolume) {
      // / empty priceScaleId = overlay on main pane, isolated margins push it to bottom strip
      volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: '',
        color: theme.volumeUp,
      })
      volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 },
      })
    }

    // / keep chart sized to container; ResizeObserver fires once on mount too
    // / bug e: guard applyOptions against disposed chart — RO callback may fire after cleanup
    let disposed = false
    const ro = new ResizeObserver(entries => {
      if (disposed) return
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        if (width > 0 && height > 0) {
          try { chart.applyOptions({ width, height }) } catch { /* disposed race */ }
        }
      }
    })
    ro.observe(container)

    setState({ chart, priceSeries, volumeSeries, isReady: true })

    return () => {
      disposed = true
      ro.disconnect()
      try {
        chart.remove()
      } catch {
        // / chart may already be disposed in strictmode re-mount race; ignore
      }
      // / no setState on unmount — next mount will set fresh state anyway, and firing
      // / setState after cleanup risks an update on an unmounted component under strictmode
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return state
}
