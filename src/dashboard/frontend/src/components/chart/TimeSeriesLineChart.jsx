import { useEffect, useMemo, useRef } from 'react'
import { useLineChart } from './useLineChart'
import { darkTheme } from './constants/theme'

// / generic lightweight-charts wrapper replacing recharts LineChart/AreaChart
// / data: [{ date|time|timestamp, value|close|...valueKey }, ...] — caller order agnostic
// / props:
// /   data        — array of rows
// /   valueKey    — which field to read the numeric value from (default 'value')
// /   timeKey     — which field holds the time (default 'date')
// /   color       — stroke color (defaults to theme.up)
// /   seriesType  — 'line' | 'area' | 'baseline' (default 'line')
// /   height      — container height in px (default 220)
// /   valueFmt    — optional formatter function (n) => string for axis / crosshair label
// /   emptyText   — text shown when no data
export default function TimeSeriesLineChart({
  data,
  valueKey = 'value',
  timeKey = 'date',
  color,
  seriesType = 'line',
  height = 220,
  valueFmt,
  emptyText = 'No data',
}) {
  const containerRef = useRef(null)

  const normalized = useMemo(
    () => normalizeSeries(data, timeKey, valueKey),
    [data, timeKey, valueKey],
  )

  const { lineSeries, chart, isReady } = useLineChart(containerRef, {
    theme: darkTheme,
    color,
    height,
    seriesType,
  })

  useEffect(() => {
    if (!isReady || !lineSeries) return
    lineSeries.setData(normalized)
    if (typeof valueFmt === 'function') {
      lineSeries.applyOptions({
        priceFormat: {
          type: 'custom',
          formatter: v => valueFmt(Number(v)),
          minMove: 0.01,
        },
      })
    }
    if (normalized.length > 0 && chart) {
      chart.timeScale().fitContent()
    }
  }, [isReady, lineSeries, normalized, chart, valueFmt])

  const hasData = normalized.length > 0

  return (
    <div className="relative" style={{ height }}>
      <div ref={containerRef} className="absolute inset-0" />
      {!hasData && (
        <div className="absolute inset-0 flex items-center justify-center text-text-muted text-sm pointer-events-none">
          {emptyText}
        </div>
      )}
    </div>
  )
}

// / coerce arbitrary {timeKey, valueKey} rows into LWC SingleValueData
// / dedupes on time, sorts ascending, drops non-finite values
// / accepts iso 'YYYY-MM-DD[THH:MM...]' strings (passed through) and unix seconds (numeric)
function normalizeSeries(data, timeKey, valueKey) {
  if (!Array.isArray(data) || data.length === 0) return []
  const seen = new Map()
  for (const row of data) {
    if (!row) continue
    const rawT = row[timeKey]
    if (rawT == null) continue
    const time = typeof rawT === 'string' ? rawT.split('T')[0] : Number(rawT)
    if (time === '' || (typeof time === 'number' && !Number.isFinite(time))) continue
    const value = Number(row[valueKey])
    if (!Number.isFinite(value)) continue
    seen.set(time, value)
  }
  return Array.from(seen.entries())
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
    .map(([time, value]) => ({ time, value }))
}
