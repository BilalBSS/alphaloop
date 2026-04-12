import { useEffect, useRef } from 'react'
import { INDICATORS, buildPaneIndexMap } from '../indicatorRegistry'

// / pure imperative writer for all requested indicators
// / single effect handles create/update/destroy: existing entries get update() not teardown,
// / so the 60s data refetch does not flicker — only newly-added ids create series, only removed ids destroy
// / per-id entry stored in ref keyed map so react re-renders don't lose chart handles
export default function IndicatorPane({ chart, priceSeries, candles, indicators, indicatorData }) {
  const entriesRef = useRef(new Map())

  useEffect(() => {
    if (!chart || !priceSeries) return undefined
    if (!Array.isArray(indicators)) return undefined

    const current = entriesRef.current
    const newIds = new Set(indicators)

    // / destroy entries for removed ids — each entry owns its own seriesList + priceLines
    for (const [id, entry] of Array.from(current.entries())) {
      if (newIds.has(id)) continue
      for (const s of entry.seriesList || []) {
        try { chart.removeSeries(s) } catch { /* / already disposed */ }
      }
      for (const pl of entry.priceLines || []) {
        try { priceSeries.removePriceLine(pl) } catch { /* / already disposed */ }
      }
      current.delete(id)
    }

    // / create for new ids, update existing — update path avoids flicker on data refetch
    const paneMap = buildPaneIndexMap(indicators)
    for (const id of indicators) {
      const spec = INDICATORS[id]
      if (!spec) continue
      const payload = indicatorData ? indicatorData[id] : null
      if (!payload) continue
      const paneIndex = paneMap[spec.pane]
      if (paneIndex === undefined) continue

      const existing = current.get(id)
      if (existing) {
        try {
          if (typeof existing.update === 'function') existing.update(payload, candles || [])
        } catch { /* / bad payload; keep old series intact */ }
        continue
      }
      try {
        const entry = spec.render(chart, payload, candles || [], paneIndex, priceSeries)
        if (entry) current.set(id, entry)
      } catch { /* / registry mismatch; skip so one bad indicator doesn't kill the chart */ }
    }

    return undefined
  }, [chart, priceSeries, candles, indicators, indicatorData])

  // / full teardown when the chart itself goes away (parent unmount, symbol change with chart remount)
  useEffect(() => {
    const current = entriesRef.current
    return () => {
      if (!chart) return
      for (const [, entry] of current.entries()) {
        for (const s of entry.seriesList || []) {
          try { chart.removeSeries(s) } catch { /* / disposed */ }
        }
        if (priceSeries) {
          for (const pl of entry.priceLines || []) {
            try { priceSeries.removePriceLine(pl) } catch { /* / disposed */ }
          }
        }
      }
      current.clear()
    }
  }, [chart, priceSeries])

  return null
}
