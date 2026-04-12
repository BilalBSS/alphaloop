import { useEffect, useRef } from 'react'
import { LineSeries } from 'lightweight-charts'

// / pair normalized overlay writer — creates two LineSeries on the price pane (pane 0) against
// / a dedicated left-side price scale so it doesn't crush the main candle axis
// / base line = subtle gray, against line = vibrant accent so the eye tracks the pair quickly
// / props: { chart, snapshot, visible }
// / snapshot shape: { base_series: [{time, value}], against_series: [{time, value}], ... }
// / when visible flips false or chart/snapshot missing: removes any existing series and returns
const COMPARE_SCALE_ID = 'compare'
const BASE_COLOR = 'rgba(180, 180, 200, 0.85)'
const AGAINST_COLOR = '#f5a623'

function _toLineData(series) {
  // / backend emits iso timestamps — convert to unix seconds, drop any bad rows
  if (!Array.isArray(series)) return []
  const out = []
  for (const row of series) {
    if (!row || row.time == null || row.value == null) continue
    const t = Math.floor(new Date(row.time).getTime() / 1000)
    const v = parseFloat(row.value)
    if (!Number.isFinite(t) || !Number.isFinite(v)) continue
    out.push({ time: t, value: v })
  }
  out.sort((a, b) => a.time - b.time)
  return out
}

export default function ComparePane({ chart, snapshot, visible }) {
  const entriesRef = useRef({ base: null, against: null })

  useEffect(() => {
    if (!chart) return undefined

    const disposeEntries = () => {
      const entries = entriesRef.current
      if (entries.base) {
        try { chart.removeSeries(entries.base) } catch { /* / already disposed */ }
        entries.base = null
      }
      if (entries.against) {
        try { chart.removeSeries(entries.against) } catch { /* / already disposed */ }
        entries.against = null
      }
    }

    if (!visible || !snapshot) {
      disposeEntries()
      return undefined
    }

    const baseData = _toLineData(snapshot.base_series)
    const againstData = _toLineData(snapshot.against_series)
    if (baseData.length === 0 && againstData.length === 0) {
      disposeEntries()
      return undefined
    }

    const entries = entriesRef.current

    // / create on first render, reuse on update so refreshes don't flicker
    if (!entries.base) {
      try {
        entries.base = chart.addSeries(LineSeries, {
          color: BASE_COLOR,
          lineWidth: 1,
          priceScaleId: COMPARE_SCALE_ID,
          priceLineVisible: false,
          lastValueVisible: false,
          title: snapshot.base || 'base',
        })
        entries.base.priceScale().applyOptions({
          visible: true,
          borderColor: '#1e1e2a',
          scaleMargins: { top: 0.08, bottom: 0.24 },
        })
      } catch { /* / series creation failed — skip this update */ }
    }
    if (!entries.against) {
      try {
        entries.against = chart.addSeries(LineSeries, {
          color: AGAINST_COLOR,
          lineWidth: 2,
          priceScaleId: COMPARE_SCALE_ID,
          priceLineVisible: false,
          lastValueVisible: true,
          title: snapshot.against || 'against',
        })
      } catch { /* / series creation failed — skip this update */ }
    }

    try {
      if (entries.base) entries.base.setData(baseData)
      if (entries.against) entries.against.setData(againstData)
    } catch { /* / bad payload; keep old series intact */ }

    return undefined
  }, [chart, snapshot, visible])

  // / full teardown when chart remounts or parent unmounts
  useEffect(() => {
    const entries = entriesRef.current
    return () => {
      if (!chart) return
      if (entries.base) {
        try { chart.removeSeries(entries.base) } catch { /* / disposed */ }
        entries.base = null
      }
      if (entries.against) {
        try { chart.removeSeries(entries.against) } catch { /* / disposed */ }
        entries.against = null
      }
    }
  }, [chart])

  return null
}
