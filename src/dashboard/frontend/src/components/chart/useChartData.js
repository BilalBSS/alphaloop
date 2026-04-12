import { useEffect, useMemo, useState } from 'react'

// / fetches intraday bars for the chart and normalizes them into lightweight-charts shape
// / legacy /api/intraday list-shape: [{ timestamp, open, high, low, close, volume, vwap }, ...]
// / new-shape /api/intraday?indicators=id1,id2: { bars: {t,o,h,l,c,v}, indicators: {...}, meta: {...} }
// / returns { bars: { candles, volume }, indicators, loading, error }
// / candles: [{ time, open, high, low, close }, ...]
// / volume: [{ time, value, color }, ...]
// / indicators: dict of id -> raw payload from backend (consumers parse per-id)
// / time is unix seconds (strictly ascending, de-duped)
// / refetches every 60s to match existing IntradayChart cadence; aborts in-flight requests on unmount
export function useChartData(symbol, timeframe = '1Hour', days = 10, indicators = []) {
  const [bars, setBars] = useState({ candles: [], volume: [] })
  const [indicatorData, setIndicatorData] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // / stable sorted csv key so effect doesn't refire on new-array-same-ids
  const idsKey = useMemo(() => {
    if (!Array.isArray(indicators) || indicators.length === 0) return ''
    return [...indicators].map(s => String(s).trim()).filter(Boolean).sort().join(',')
  }, [indicators])

  useEffect(() => {
    if (!symbol) return undefined
    const ctrl = new AbortController()
    let alive = true

    const fetchOnce = async () => {
      try {
        const base = `/api/intraday/${symbol}?timeframe=${timeframe}&days=${days}`
        const url = idsKey ? `${base}&indicators=${encodeURIComponent(idsKey)}` : base
        const resp = await fetch(url, { signal: ctrl.signal })
        if (!resp.ok) throw new Error(`${resp.status}`)
        const json = await resp.json()
        if (!alive) return

        // / two shapes: legacy array or new-shape object with bars/indicators/meta
        let rawRows
        let nextIndicators = {}
        if (Array.isArray(json)) {
          rawRows = json.map(r => ({
            ts: r.timestamp,
            o: parseFloat(r.open),
            h: parseFloat(r.high),
            l: parseFloat(r.low),
            c: parseFloat(r.close),
            v: parseFloat(r.volume || 0),
          }))
        } else if (json && json.bars) {
          const b = json.bars
          const n = Array.isArray(b.t) ? b.t.length : 0
          rawRows = new Array(n)
          for (let i = 0; i < n; i++) {
            rawRows[i] = {
              ts: b.t[i],
              o: parseFloat(b.o[i]),
              h: parseFloat(b.h[i]),
              l: parseFloat(b.l[i]),
              c: parseFloat(b.c[i]),
              v: parseFloat(b.v[i] || 0),
            }
          }
          nextIndicators = json.indicators || {}
        } else {
          rawRows = []
        }

        // / normalize + de-dupe by unix seconds, then sort ascending (lightweight-charts requires it)
        // / track original index so indicator arrays can be aligned to the sorted candle order
        const seen = new Map()
        for (let i = 0; i < rawRows.length; i++) {
          const r = rawRows[i]
          if (!r.ts) continue
          const t = Math.floor(new Date(r.ts).getTime() / 1000)
          if (!Number.isFinite(t)) continue
          if (![r.o, r.h, r.l, r.c].every(Number.isFinite)) continue
          seen.set(t, { t, o: r.o, h: r.h, l: r.l, c: r.c, v: r.v, srcIdx: i })
        }
        const sorted = Array.from(seen.values()).sort((a, b) => a.t - b.t)
        const srcOrder = sorted.map(r => r.srcIdx)

        const candles = sorted.map(r => ({ time: r.t, open: r.o, high: r.h, low: r.l, close: r.c }))
        const volume = sorted.map(r => ({
          time: r.t,
          value: r.v,
          color: r.c >= r.o ? 'rgba(0, 220, 130, 0.5)' : 'rgba(255, 71, 87, 0.5)',
        }))
        setBars({ candles, volume })

        // / align indicator series arrays to the sorted/deduped candle order so times match 1:1
        // / horizontal_levels don't need alignment — they are scalar levels, not per-bar
        const alignedIndicators = {}
        for (const [id, payload] of Object.entries(nextIndicators)) {
          if (!payload || typeof payload !== 'object') continue
          if (payload.kind === 'series') {
            const aligned = { ...payload }
            for (const k of Object.keys(payload)) {
              const v = payload[k]
              if (Array.isArray(v) && v.length === rawRows.length) {
                aligned[k] = srcOrder.map(i => v[i])
              }
            }
            alignedIndicators[id] = aligned
          } else {
            alignedIndicators[id] = payload
          }
        }
        setIndicatorData(alignedIndicators)
        setError(null)
      } catch (err) {
        if (err.name === 'AbortError' || !alive) return
        setError(err.message || String(err))
      } finally {
        if (alive) setLoading(false)
      }
    }

    fetchOnce()
    const id = setInterval(fetchOnce, 60000)
    return () => {
      alive = false
      clearInterval(id)
      ctrl.abort()
    }
  }, [symbol, timeframe, days, idsKey])

  return { bars, indicators: indicatorData, loading, error }
}
