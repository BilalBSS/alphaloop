import { useEffect, useRef, useState } from 'react'

// / horizontal volume-at-price histogram hook — fetches /api/volume-profile/{symbol}
// / returns { profile, loading }
// / profile shape: { symbol, bins: [{price_low, price_high, volume, pct}], poc, vah, val, total_volume }
// / refreshes every 60s while enabled; aborts in-flight fetch on unmount / param change
const REFRESH_MS = 60000

export function useVolumeProfile(symbol, bins = 24, days = 30, timeframe = '1Hour', enabled = false) {
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(false)
  const ctrlRef = useRef(null)

  useEffect(() => {
    if (!enabled || !symbol) {
      if (ctrlRef.current) {
        ctrlRef.current.abort()
        ctrlRef.current = null
      }
      setProfile(null)
      setLoading(false)
      return undefined
    }

    let alive = true

    const fetchOnce = async () => {
      if (ctrlRef.current) ctrlRef.current.abort()
      const ctrl = new AbortController()
      ctrlRef.current = ctrl
      setLoading(true)
      try {
        const url = `/api/volume-profile/${encodeURIComponent(symbol)}?bins=${bins}&days=${days}&timeframe=${encodeURIComponent(timeframe)}`
        const resp = await fetch(url, { signal: ctrl.signal })
        if (!resp.ok) throw new Error(`${resp.status}`)
        const json = await resp.json()
        if (!alive) return
        setProfile(json)
      } catch (err) {
        if (err.name === 'AbortError' || !alive) return
        setProfile(null)
      } finally {
        if (alive) setLoading(false)
      }
    }

    fetchOnce()
    const id = setInterval(fetchOnce, REFRESH_MS)
    return () => {
      alive = false
      clearInterval(id)
      if (ctrlRef.current) {
        ctrlRef.current.abort()
        ctrlRef.current = null
      }
    }
  }, [symbol, bins, days, timeframe, enabled])

  return { profile, loading }
}
