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
      // / area series: same line + translucent fill beneath
      lineSeries = chart.addSeries(AreaSeries, {
        lineColor: color || theme.up,
        topColor: color ? hexToRgba(color, 0.28) : 'rgba(0, 220, 130, 0.28)',
        bottomColor: color ? hexToRgba(color, 0) : 'rgba(0, 220, 130, 0)',
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      })
    } else if (seriesType === 'baseline') {
      // / baseline series: auto-split above/below baseValue with distinct top/bottom palettes
      lineSeries = chart.addSeries(BaselineSeries, {
        baseValue: { type: 'price', price: 0 },
        topLineColor: theme.up,
        topFillColor1: 'rgba(0, 220, 130, 0.28)',
        topFillColor2: 'rgba(0, 220, 130, 0.05)',
        bottomLineColor: theme.down,
        bottomFillColor1: 'rgba(255, 71, 87, 0.05)',
        bottomFillColor2: 'rgba(255, 71, 87, 0.28)',
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
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height: h } = entry.contentRect
        if (width > 0 && h > 0) chart.applyOptions({ width, height: h })
      }
    })
    ro.observe(container)

    setState({ chart, lineSeries, isReady: true })

    return () => {
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

// / convert #rrggbb to rgba(r, g, b, a) — used for area fill gradient derived from a solid stroke
function hexToRgba(hex, alpha) {
  if (typeof hex !== 'string' || !hex.startsWith('#') || hex.length !== 7) {
    return `rgba(0, 220, 130, ${alpha})`
  }
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}
