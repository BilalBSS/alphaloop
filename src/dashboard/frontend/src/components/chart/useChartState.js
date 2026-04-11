import { useCallback, useEffect, useRef, useState } from 'react'

// / per-symbol chart state hook: fetches on mount + symbol change, saves with 400ms debounce
// / state shape: { symbol, timeframe, active_indicators, indicator_params }
// / exposes: { state, loading, save, toggleIndicator }
// /   save({ timeframe?, active_indicators?, indicator_params? }) merges into local state then posts
// /   toggleIndicator(id) flips id membership in active_indicators
// / symbol changes flush the pending patch against the OLD symbol before fetching the new
// / the GET on mount is guarded by a generation counter + dirty flag so a late response cannot
// / clobber an optimistic local toggle made while the fetch was in flight
const DEBOUNCE_MS = 400

const EMPTY_STATE = (symbol) => ({
  symbol,
  timeframe: '1Hour',
  active_indicators: [],
  indicator_params: {},
})

// / post without awaiting — used on symbol switch to fire the old symbol's pending patch
function _firePatch(sym, data) {
  if (!sym || !data) return
  try {
    fetch(`/api/chart-state/${sym}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }).catch(() => {})
  } catch {
    // / fetch unavailable — swallow
  }
}

export function useChartState(symbol) {
  const [state, setState] = useState(() => EMPTY_STATE(symbol))
  const [loading, setLoading] = useState(true)
  const saveTimer = useRef(null)
  // / pending patch is tagged with the symbol it belongs to, never bleeds across symbols
  const pendingPatch = useRef(null)
  const aliveRef = useRef(true)
  // / generation counter so late GET responses from a prior symbol cannot overwrite current state
  const genRef = useRef(0)
  // / dirty flag set when user has optimistically touched local state; suppresses GET overwrite
  const dirtyRef = useRef(false)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      if (saveTimer.current) {
        clearTimeout(saveTimer.current)
        saveTimer.current = null
      }
      // / unmount: fire any queued patch for whatever symbol it's tagged with
      const patch = pendingPatch.current
      pendingPatch.current = null
      if (patch) _firePatch(patch.symbol, patch.data)
    }
  }, [])

  // / fetch on mount + symbol change; cleanup flushes prior symbol's pending patch
  useEffect(() => {
    if (!symbol) return undefined
    const myGen = ++genRef.current
    dirtyRef.current = false
    setLoading(true)
    setState(EMPTY_STATE(symbol))
    const ctrl = new AbortController()
    ;(async () => {
      try {
        const resp = await fetch(`/api/chart-state/${symbol}`, { signal: ctrl.signal })
        if (!resp.ok) throw new Error(`${resp.status}`)
        const json = await resp.json()
        if (!aliveRef.current) return
        // / superseded by a later symbol change — drop the response
        if (myGen !== genRef.current) return
        // / user already toggled locally — don't clobber their optimistic state
        if (dirtyRef.current) return
        if (json && typeof json === 'object' && json.symbol && json.symbol !== symbol) return
        setState({
          symbol: (json && json.symbol) || symbol,
          timeframe: (json && json.timeframe) || '1Hour',
          active_indicators: json && Array.isArray(json.active_indicators) ? json.active_indicators : [],
          indicator_params: json && json.indicator_params && typeof json.indicator_params === 'object' ? json.indicator_params : {},
        })
      } catch (err) {
        if (err.name === 'AbortError') return
        if (!aliveRef.current) return
        if (myGen !== genRef.current) return
        if (dirtyRef.current) return
        setState(EMPTY_STATE(symbol))
      } finally {
        if (aliveRef.current && myGen === genRef.current) setLoading(false)
      }
    })()
    return () => {
      ctrl.abort()
      // / symbol is changing — flush any queued patch for the OLD symbol synchronously
      if (saveTimer.current) {
        clearTimeout(saveTimer.current)
        saveTimer.current = null
      }
      const patch = pendingPatch.current
      pendingPatch.current = null
      if (patch) _firePatch(patch.symbol, patch.data)
    }
  }, [symbol])

  // / flush current pending patch against its tagged symbol
  const flush = useCallback(async () => {
    const entry = pendingPatch.current
    pendingPatch.current = null
    saveTimer.current = null
    if (!entry || !entry.symbol) return
    try {
      const ctrl = new AbortController()
      const timeoutId = setTimeout(() => ctrl.abort(), 10000)
      try {
        await fetch(`/api/chart-state/${entry.symbol}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(entry.data),
          signal: ctrl.signal,
        })
      } finally {
        clearTimeout(timeoutId)
      }
    } catch {
      // / swallow — optimistic state already reflected locally
    }
  }, [])

  // / merge a patch into the pending queue, tagged with current symbol, and debounce the flush
  const _queuePatch = useCallback((patch) => {
    dirtyRef.current = true
    const prev = pendingPatch.current
    // / if prior patch was for a different symbol, flush it first so we never bleed fields across symbols
    if (prev && prev.symbol !== symbol) {
      _firePatch(prev.symbol, prev.data)
      pendingPatch.current = { symbol, data: { ...patch } }
    } else {
      pendingPatch.current = {
        symbol,
        data: { ...(prev && prev.data ? prev.data : {}), ...patch },
      }
    }
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(flush, DEBOUNCE_MS)
  }, [flush, symbol])

  const save = useCallback((patch) => {
    if (!patch || typeof patch !== 'object') return
    // / optimistic local merge so ui reflects change instantly
    setState(prev => ({
      ...prev,
      ...('timeframe' in patch ? { timeframe: patch.timeframe } : {}),
      ...('active_indicators' in patch ? { active_indicators: patch.active_indicators } : {}),
      ...('indicator_params' in patch ? { indicator_params: patch.indicator_params } : {}),
    }))
    _queuePatch(patch)
  }, [_queuePatch])

  const toggleIndicator = useCallback((id) => {
    if (typeof id !== 'string' || !id) return
    setState(prev => {
      const current = Array.isArray(prev.active_indicators) ? prev.active_indicators : []
      const exists = current.includes(id)
      const next = exists ? current.filter(x => x !== id) : [...current, id]
      _queuePatch({ active_indicators: next })
      return { ...prev, active_indicators: next }
    })
  }, [_queuePatch])

  return { state, loading, save, toggleIndicator }
}
