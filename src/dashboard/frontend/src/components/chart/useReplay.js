import { useEffect, useRef, useState } from 'react'

// / observation-mode replay hook — fetches /api/replay/{symbol}?cutoff=... with 300ms debounce
// / returns { snapshot, loading, error }
// / snapshot shape matches backend: { symbol, cutoff, min_t, max_t, bars: {t,o,h,l,c,v}, trades, signals, consensus }
// / when enabled=false: no fetch, snapshot stays null so the chart can fall back to live data
// / aborts in-flight fetches on unmount / new cutoff so rapid slider scrubbing never races
const DEBOUNCE_MS = 300

export function useReplay(symbol, cutoff, enabled) {
  const [snapshot, setSnapshot] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const timerRef = useRef(null)
  const ctrlRef = useRef(null)

  useEffect(() => {
    // / disabled or missing inputs -> clear snapshot, skip fetch
    if (!enabled || !symbol || !cutoff) {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
      if (ctrlRef.current) {
        ctrlRef.current.abort()
        ctrlRef.current = null
      }
      setSnapshot(null)
      setLoading(false)
      setError(null)
      return undefined
    }

    // / debounce so scrubbing the slider doesn't hammer the backend
    if (timerRef.current) clearTimeout(timerRef.current)
    if (ctrlRef.current) ctrlRef.current.abort()

    let alive = true
    setLoading(true)

    timerRef.current = setTimeout(async () => {
      const ctrl = new AbortController()
      ctrlRef.current = ctrl
      try {
        const url = `/api/replay/${symbol}?cutoff=${encodeURIComponent(cutoff)}&days_back=30`
        const resp = await fetch(url, { signal: ctrl.signal })
        if (!resp.ok) throw new Error(`${resp.status}`)
        const json = await resp.json()
        if (!alive) return
        setSnapshot(json)
        setError(null)
      } catch (err) {
        if (err.name === 'AbortError' || !alive) return
        setError(err.message || String(err))
      } finally {
        if (alive) setLoading(false)
      }
    }, DEBOUNCE_MS)

    return () => {
      alive = false
      if (timerRef.current) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
      if (ctrlRef.current) {
        ctrlRef.current.abort()
        ctrlRef.current = null
      }
    }
  }, [symbol, cutoff, enabled])

  return { snapshot, loading, error }
}
