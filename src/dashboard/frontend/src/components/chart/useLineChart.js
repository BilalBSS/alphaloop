import { useEffect, useState } from 'react'
import { createChart, LineSeries, AreaSeries, BaselineSeries } from 'lightweight-charts'
import { chartLayoutOptions } from './constants/theme'

// / generic single-series chart lifecycle hook for line / area / baseline series
// / mirrors useCandlestickChart: creates chart once, cleans up once, resize observer, strictmode safe
// / seriesType: 'line' | 'area' | 'baseline' — baseline auto-splits above/below zero with green/red fill
export function useLineChart(containerRef, { theme, color, height = 220, seriesType = 'line' } = {}) {
  const [state, setState] = useState({ chart: null, lineSeries: null, isReady: false })

  useEffect(() => {
    const container = containerRef.current
    if (!container) return undefined

    const chart = createChart(container, {
      width: container.clientWidth || 600,
      height: container.clientHeight || height,
      ...chartLayoutOptions(theme),
      // / line charts don't need the ticker-style timescale the candle chart uses
      timeScale: {
        borderColor: theme.border,
        timeVisible: true,
        secondsVisible: false,
      },
    })

    let lineSeries
    if (seriesType === 'area') {
      // / area + translucent fill
      const stroke = color || theme.up
      lineSeries = chart.addSeries(AreaSeries, {
        lineColor: stroke,
        topColor: hexToRgba(stroke, 0.28),
        bottomColor: hexToRgba(stroke, 0),
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      })
    } else if (seriesType === 'baseline') {
      // / baseline split by tone
      lineSeries = chart.addSeries(BaselineSeries, {
        baseValue: { type: 'price', price: 0 },
        topLineColor: theme.up,
        topFillColor1: hexToRgba(theme.up, 0.28),
        topFillColor2: hexToRgba(theme.up, 0.05),
        bottomLineColor: theme.down,
        bottomFillColor1: hexToRgba(theme.down, 0.05),
        bottomFillColor2: hexToRgba(theme.down, 0.28),
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      })
    } else {
      // / plain line series
      lineSeries = chart.addSeries(LineSeries, {
        color: color || theme.up,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      })
    }

    // / chart sized to container; ResizeObserver fires once on mount too
    // / bug e: guard applyOptions against disposed chart — RO callback may fire after cleanup
    let disposed = false
    const ro = new ResizeObserver(entries => {
      if (disposed) return
      for (const entry of entries) {
        const { width, height: h } = entry.contentRect
        if (width > 0 && h > 0) {
          try { chart.applyOptions({ width, height: h }) } catch { /* disposed race */ }
        }
      }
    })
    ro.observe(container)

    setState({ chart, lineSeries, isReady: true })

    return () => {
      disposed = true
      ro.disconnect()
      try {
        chart.remove()
      } catch {
        // / chart may already be disposed in strictmode re-mount race; ignore
      }
      // / no setState on unmount — next mount will set fresh state anyway, same rule as useCandlestickChart
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return state
}

// / hex to rgba
function hexToRgba(hex, alpha) {
  if (typeof hex !== 'string' || !hex.startsWith('#') || hex.length !== 7) {
    return `rgba(127, 184, 122, ${alpha})`
  }
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}
