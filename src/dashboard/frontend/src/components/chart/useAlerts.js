import { useCallback, useEffect, useRef, useState } from 'react'

// / price-cross alert hook: fetches on mount + symbol change, auto-refreshes every 30s
// / shape: { alerts, loading, create, update, remove, refresh }
// /   create(price, direction, label) POSTs a new active alert
// /   update(id, patch) PUTs a partial patch ({ price?, direction?, label?, status? })
// /   remove(id) DELETEs the row
// /   refresh() forces an out-of-band list refetch
// / stale-fetch guard: every symbol change bumps a generation counter, and late responses
// / from the previous symbol are dropped before they can clobber the new state

const REFRESH_MS = 30000

export function useAlerts(symbol) {
  const [alerts, setAlerts] = useState([])
  const [loading, setLoading] = useState(true)
  const aliveRef = useRef(true)
  const genRef = useRef(0)

  useEffect(() => () => { aliveRef.current = false }, [])

  useEffect(() => {
    if (!symbol) return undefined
    const myGen = ++genRef.current
    setLoading(true)
    const ctrl = new AbortController()

    const fetchOnce = async () => {
      try {
        const resp = await fetch(`/api/alerts/${symbol}`, { signal: ctrl.signal })
        if (!resp.ok) throw new Error(`${resp.status}`)
        const json = await resp.json()
        if (!aliveRef.current) return
        if (myGen !== genRef.current) return
        setAlerts(Array.isArray(json) ? json : [])
      } catch (err) {
        if (err && err.name === 'AbortError') return
        if (!aliveRef.current) return
        if (myGen !== genRef.current) return
        setAlerts([])
      } finally {
        if (aliveRef.current && myGen === genRef.current) setLoading(false)
      }
    }

    fetchOnce()
    const t = setInterval(fetchOnce, REFRESH_MS)
    return () => {
      clearInterval(t)
      ctrl.abort()
    }
  }, [symbol])

  const create = useCallback(async (price, direction, label) => {
    if (!symbol) return null
    try {
      const resp = await fetch(`/api/alerts/${symbol}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ price, direction, label: label || null }),
      })
      if (!resp.ok) return null
      const row = await resp.json()
      if (row && row.id && aliveRef.current) {
        setAlerts(prev => [row, ...prev])
      }
      return row
    } catch {
      return null
    }
  }, [symbol])

  const update = useCallback(async (id, patch) => {
    if (!symbol || !id || !patch) return null
    try {
      const resp = await fetch(`/api/alerts/${symbol}/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!resp.ok) return null
      const row = await resp.json()
      if (row && row.id && aliveRef.current) {
        setAlerts(prev => prev.map(a => (a.id === row.id ? row : a)))
      }
      return row
    } catch {
      return null
    }
  }, [symbol])

  const remove = useCallback(async (id) => {
    if (!symbol || !id) return false
    try {
      const resp = await fetch(`/api/alerts/${symbol}/${id}`, { method: 'DELETE' })
      if (!resp.ok) return false
      if (aliveRef.current) setAlerts(prev => prev.filter(a => a.id !== id))
      return true
    } catch {
      return false
    }
  }, [symbol])

  // / imperative refresh — callers that add/update/remove already mutate state optimistically,
  // / so this is a one-shot refetch; still generation-guarded to drop stale responses
  const refresh = useCallback(async () => {
    if (!symbol) return
    const myGen = genRef.current
    try {
      const resp = await fetch(`/api/alerts/${symbol}`)
      if (!resp.ok) return
      const json = await resp.json()
      if (!aliveRef.current || myGen !== genRef.current) return
      setAlerts(Array.isArray(json) ? json : [])
    } catch {
      // / swallow
    }
  }, [symbol])

  return { alerts, loading, create, update, remove, refresh }
}
