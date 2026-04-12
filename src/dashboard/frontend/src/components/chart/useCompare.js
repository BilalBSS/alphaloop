import { useEffect, useRef, useState } from 'react'

// / pair normalized overlay hook — fetches /api/compare and refreshes every 60s while enabled
// / returns { snapshot, loading }
// / snapshot shape matches backend: { base, against, timeframe, days, base_series, against_series, common_count }
// / when enabled is false: clears snapshot, aborts in-flight fetch, skips refresh interval
const REFRESH_MS = 60000

export function useCompare(base, against, timeframe = '1Day', days = 90, enabled = false) {
  const [snapshot, setSnapshot] = useState(null)
  const [loading, setLoading] = useState(false)
  const ctrlRef = useRef(null)

  useEffect(() => {
    // / disabled or missing inputs -> clear snapshot, skip fetch
    if (!enabled || !base || !against) {
      if (ctrlRef.current) {
        ctrlRef.current.abort()
        ctrlRef.current = null
      }
      setSnapshot(null)
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
        const url = `/api/compare?base=${encodeURIComponent(base)}&against=${encodeURIComponent(against)}&timeframe=${encodeURIComponent(timeframe)}&days=${days}`
        const resp = await fetch(url, { signal: ctrl.signal })
        if (!resp.ok) throw new Error(`${resp.status}`)
        const json = await resp.json()
        if (!alive) return
        setSnapshot(json)
      } catch (err) {
        if (err.name === 'AbortError' || !alive) return
        setSnapshot(null)
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
  }, [base, against, timeframe, days, enabled])

  return { snapshot, loading }
}
