import { useEffect, useMemo, useState } from 'react'

// / fetches marker events for a symbol from /api/markers and refreshes on a 60s cadence
// / returns { markers, loading } — markers is a dict keyed by kind (trades, signals, insiders, earnings, regime, consensus)
// / aborts in-flight requests on unmount or when symbol/kinds change
// / accepts a stable kinds array — caller must memoize upstream to avoid refetch storms
export function useMarkers(symbol, kinds) {
  const [markers, setMarkers] = useState({
    trades: [],
    signals: [],
    insiders: [],
    earnings: [],
    regime: [],
    consensus: [],
  })
  const [loading, setLoading] = useState(true)

  // / stable csv key so effect doesn't refire on new-array-same-kinds
  const kindsKey = useMemo(() => {
    if (!Array.isArray(kinds) || kinds.length === 0) return ''
    return [...kinds].map(s => String(s).trim()).filter(Boolean).sort().join(',')
  }, [kinds])

  useEffect(() => {
    if (!symbol || !kindsKey) {
      setLoading(false)
      return undefined
    }
    const ctrl = new AbortController()
    let alive = true

    const fetchOnce = async () => {
      try {
        const resp = await fetch(`/api/markers/${symbol}?kinds=${encodeURIComponent(kindsKey)}`, {
          signal: ctrl.signal,
        })
        if (!resp.ok) throw new Error(`${resp.status}`)
        const json = await resp.json()
        if (!alive) return
        // / fill missing kinds with empty arrays so consumers can destructure safely
        setMarkers({
          trades: Array.isArray(json.trades) ? json.trades : [],
          signals: Array.isArray(json.signals) ? json.signals : [],
          insiders: Array.isArray(json.insiders) ? json.insiders : [],
          earnings: Array.isArray(json.earnings) ? json.earnings : [],
          regime: Array.isArray(json.regime) ? json.regime : [],
          consensus: Array.isArray(json.consensus) ? json.consensus : [],
        })
      } catch (err) {
        if (err.name === 'AbortError' || !alive) return
        // / swallow network errors; markers stay at last good state
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
  }, [symbol, kindsKey])

  return { markers, loading }
}
