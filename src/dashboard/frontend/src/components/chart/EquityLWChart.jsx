import { useEffect, useMemo, useRef } from 'react'
import { useLineChart } from './useLineChart'
import { darkTheme } from './constants/theme'

// / lightweight-charts replacement for the recharts equity curve in PortfolioTab
// / accepts the raw /api/equity-history response shape: { timestamps: number[], equity: number[] }
// / or a pre-zipped [{ ts, equity }] array — both are normalized to LWC SingleValueData
export default function EquityLWChart({ data, height = 200 }) {
  const containerRef = useRef(null)

  // / determine trend direction up-front so the series color reflects the whole range, matching the old chart
  const normalized = useMemo(() => normalizeEquity(data), [data])
  const isUp = normalized.length > 1
    ? normalized[normalized.length - 1].value >= normalized[0].value
    : true
  const color = isUp ? darkTheme.up : darkTheme.down

  const { lineSeries, chart, isReady } = useLineChart(containerRef, {
    theme: darkTheme,
    color,
    height,
    seriesType: 'area',
  })

  // / push data imperatively when it arrives / changes
  useEffect(() => {
    if (!isReady || !lineSeries) return
    lineSeries.setData(normalized)
    lineSeries.applyOptions({
      lineColor: color,
      topColor: color === darkTheme.up ? 'rgba(0, 220, 130, 0.28)' : 'rgba(255, 71, 87, 0.28)',
      bottomColor: color === darkTheme.up ? 'rgba(0, 220, 130, 0)' : 'rgba(255, 71, 87, 0)',
      priceFormat: {
        type: 'custom',
        formatter: v => '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
        minMove: 0.01,
      },
    })
    if (normalized.length > 0 && chart) {
      chart.timeScale().fitContent()
    }
  }, [isReady, lineSeries, normalized, color, chart])

  const hasData = normalized.length > 0

  return (
    <div className="relative" style={{ height }}>
      <div ref={containerRef} className="absolute inset-0" />
      {!hasData && (
        <div className="absolute inset-0 flex items-center justify-center text-text-muted text-sm pointer-events-none">
          No equity data
        </div>
      )}
    </div>
  )
}

// / accept either { timestamps, equity } (api shape) or [{ ts, equity }] and coerce to LWC SingleValueData
// / dedupe on time, sort ascending, drop invalid points
function normalizeEquity(data) {
  if (!data) return []
  let pairs = []
  if (Array.isArray(data.timestamps) && Array.isArray(data.equity)) {
    const n = Math.min(data.timestamps.length, data.equity.length)
    for (let i = 0; i < n; i++) pairs.push([data.timestamps[i], data.equity[i]])
  } else if (Array.isArray(data)) {
    for (const row of data) {
      const t = row?.ts ?? row?.time ?? row?.timestamp
      const v = row?.equity ?? row?.value
      if (t != null && v != null) pairs.push([t, v])
    }
  } else {
    return []
  }

  const seen = new Map()
  for (const [t, v] of pairs) {
    const time = Number(t)
    const value = Number(v)
    if (!Number.isFinite(time) || !Number.isFinite(value)) continue
    seen.set(time, value)
  }
  const out = Array.from(seen.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([time, value]) => ({ time, value }))
  return out
}
