import { useEffect, useMemo, useRef } from 'react'
import { useLineChart } from './useLineChart'
import { darkTheme } from './constants/theme'

// / lightweight-charts replacement for the recharts news-sentiment bar chart in SymbolDetail
// / uses a BaselineSeries so values auto-split green above 0 and red below 0
// / data: [{ date, sentiment_score }, ...] where score is in [-1, 1] (api returns newest-first — caller order agnostic)
export default function NewsSentimentLWChart({ data, height = 140 }) {
  const containerRef = useRef(null)

  const normalized = useMemo(() => normalizeSentiment(data), [data])

  const { lineSeries, chart, isReady } = useLineChart(containerRef, {
    theme: darkTheme,
    height,
    seriesType: 'baseline',
  })

  useEffect(() => {
    if (!isReady || !lineSeries) return
    lineSeries.setData(normalized)
    lineSeries.applyOptions({
      priceFormat: {
        type: 'custom',
        formatter: v => Number(v).toFixed(3),
        minMove: 0.001,
      },
    })
    if (normalized.length > 0 && chart) {
      chart.timeScale().fitContent()
      // / fix axis range so the ±1 sentiment scale renders consistently regardless of actual extrema
      lineSeries.priceScale().applyOptions({
        autoScale: false,
        scaleMargins: { top: 0.1, bottom: 0.1 },
      })
      lineSeries.applyOptions({
        autoscaleInfoProvider: () => ({
          priceRange: { minValue: -1, maxValue: 1 },
        }),
      })
    }
  }, [isReady, lineSeries, normalized, chart])

  const hasData = normalized.length > 0

  return (
    <div className="relative" style={{ height }}>
      <div ref={containerRef} className="absolute inset-0" />
      {!hasData && (
        <div className="absolute inset-0 flex items-center justify-center text-text-muted text-sm pointer-events-none">
          No news sentiment data
        </div>
      )}
    </div>
  )
}

// / accept [{ date, sentiment_score }] in any order and coerce to LWC SingleValueData
// / lightweight-charts accepts ISO 'YYYY-MM-DD' strings natively as Time, so we pass them through
// / dedupe on date, sort ascending, drop invalid rows
function normalizeSentiment(data) {
  if (!Array.isArray(data) || data.length === 0) return []
  const seen = new Map()
  for (const row of data) {
    const rawDate = row?.date
    if (!rawDate) continue
    const day = typeof rawDate === 'string' ? rawDate.split('T')[0] : String(rawDate)
    const value = Number(row.sentiment_score ?? row.score)
    if (!Number.isFinite(value)) continue
    // / clamp to [-1, 1] in case backend returns something out of range
    const clamped = Math.max(-1, Math.min(1, value))
    seen.set(day, clamped)
  }
  return Array.from(seen.entries())
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
    .map(([time, value]) => ({ time, value }))
}
