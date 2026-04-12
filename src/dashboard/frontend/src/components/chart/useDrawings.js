import { createContext, createElement, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'

// / drawing persistence hook for lightweight-charts-drawing
// / owns the DrawingManager lifecycle, hydrates from /api/drawings/{symbol}, persists on add/update/delete
// / gh issue #1920 workaround: manager mounts once in an empty-deps useEffect, never held outside,
// / subscribeVisibleTimeRangeChange pokes requestUpdate() on each primitive to kill drift on pan/zoom
// / if the library fails to import at runtime we return a no-op shape so the chart still renders
//
// / shape: { drawings, activeTool, setTool, clear, undo }
// /   drawings   — list of { id, drawing_type, payload } (local echo of server state)
// /   activeTool — current tool string (null when cursor / none)
// /   setTool    — activate a tool by name, null to deselect
// /   clear      — remove every drawing locally + server side
// /   undo       — remove the most recently added drawing

const DEBOUNCE_MS = 400

// / map lightweight-charts-drawing internal type strings to our backend whitelist
// / kebab -> snake so the db drawing_type column stays readable + consistent with python side
const TYPE_TO_DB = {
  'trend-line': 'trendline',
  'horizontal-line': 'horizontal_line',
  'vertical-line': 'vertical_line',
  'rectangle': 'rectangle',
  'fib-retracement': 'fib_retracement',
  'fib-extension': 'fib_extension',
  'text-annotation': 'text',
  'arrow': 'arrow',
  'ray': 'ray',
  'price-range': 'price_range',
  'brush': 'brush',
}

// / reverse map for hydration — server drawing_type back into a library type string
const DB_TO_TYPE = Object.fromEntries(Object.entries(TYPE_TO_DB).map(([k, v]) => [v, k]))

// / dynamic import so a missing library degrades to a no-op hook instead of crashing the chart
let _libPromise = null
function _loadLib() {
  if (_libPromise) return _libPromise
  _libPromise = import('lightweight-charts-drawing').catch(err => {
    // / log once and surface null so the caller knows to no-op
    console.warn('[useDrawings] lightweight-charts-drawing failed to load', err && err.message)
    return null
  })
  return _libPromise
}

// / generate a stable client-side id for new drawings until the server assigns one
let _clientIdCounter = 0
const _newClientId = () => `local-${++_clientIdCounter}-${Date.now()}`

export function useDrawings(chart, priceSeries, symbol) {
  const [drawings, setDrawings] = useState([])
  const [activeTool, setActiveTool] = useState(null)
  const managerRef = useRef(null)
  const containerRef = useRef(null)
  const drawingsRef = useRef([])
  const updateTimersRef = useRef(new Map())
  const aliveRef = useRef(true)
  // / map lib drawing id -> server id so update/delete can target the server row
  const idMapRef = useRef(new Map())

  // / keep drawingsRef synced so callbacks can read the latest list without re-subscribing
  useEffect(() => { drawingsRef.current = drawings }, [drawings])

  useEffect(() => () => {
    aliveRef.current = false
    for (const t of updateTimersRef.current.values()) clearTimeout(t)
    updateTimersRef.current.clear()
  }, [])

  // / mount the drawing manager ONCE per (chart, priceSeries, symbol) triple
  // / cleanup detaches on unmount — never expose chart ref outside this effect scope
  // / race fix: manager / handler unsubscribes live in the outer closure so the cleanup
  // / can detach even when it fires before the async IIFE finishes wiring
  useEffect(() => {
    if (!chart || !priceSeries || !symbol) return undefined
    let cancelled = false
    let manager = null
    let unsubRange = null
    let offAdded = null
    let offUpdated = null
    let offRemoved = null
    let offToolChanged = null

    const doDetach = () => {
      try { offAdded && offAdded() } catch { /* / gone */ }
      try { offUpdated && offUpdated() } catch { /* / gone */ }
      try { offRemoved && offRemoved() } catch { /* / gone */ }
      try { offToolChanged && offToolChanged() } catch { /* / gone */ }
      if (unsubRange) {
        try { unsubRange() } catch { /* / gone */ }
        unsubRange = null
      }
      if (manager) {
        try { manager.detach() } catch { /* / disposed */ }
        manager = null
      }
      if (managerRef.current) managerRef.current = null
      idMapRef.current.clear()
      for (const t of updateTimersRef.current.values()) clearTimeout(t)
      updateTimersRef.current.clear()
    }

    ;(async () => {
      const lib = await _loadLib()
      if (cancelled || !lib || !lib.DrawingManager) return
      // / require a real chart container — bailing on the document.body fallback avoids attaching
      // / hit-tester listeners to the whole page if the chart element is unavailable
      const container = chart && chart.chartElement && chart.chartElement()
      if (!container) {
        console.warn('[useDrawings] chart element unavailable, drawing manager not attached')
        return
      }
      try {
        manager = new lib.DrawingManager()
        containerRef.current = container
        manager.attach(chart, priceSeries, container)
      } catch (err) {
        console.warn('[useDrawings] manager.attach failed', err && err.message)
        manager = null
        return
      }
      if (cancelled) {
        try { manager.detach() } catch { /* / disposed */ }
        manager = null
        return
      }
      managerRef.current = manager

      // / wire server persistence callbacks via DrawingManager events
      offAdded = manager.on('drawing:added', (evt) => {
        const drawing = evt && evt.drawing
        if (!drawing) return
        const libType = drawing.type
        const dbType = TYPE_TO_DB[libType]
        if (!dbType) {
          // / surface unmapped tool types so silent drops are visible during rollout
          console.warn('[useDrawings] unmapped drawing type dropped', libType)
          return
        }
        // / skip drawings we re-inserted during hydration (they already carry an id map entry)
        if (idMapRef.current.has(drawing.id)) return
        let payload
        try {
          payload = typeof drawing.toJSON === 'function' ? drawing.toJSON() : null
        } catch { payload = null }
        if (!payload) return
        _postCreate(symbol, dbType, payload).then(serverRow => {
          if (!aliveRef.current || !serverRow || !serverRow.id) return
          idMapRef.current.set(drawing.id, serverRow.id)
          setDrawings(prev => [...prev, { id: serverRow.id, drawing_type: dbType, payload, libId: drawing.id }])
        }).catch(() => {})
      })

      offUpdated = manager.on('drawing:updated', (evt) => {
        const drawing = evt && evt.drawing
        if (!drawing) return
        const serverId = idMapRef.current.get(drawing.id)
        if (!serverId) return
        let payload
        try {
          payload = typeof drawing.toJSON === 'function' ? drawing.toJSON() : null
        } catch { payload = null }
        if (!payload) return
        // / debounce updates per drawing — dragging an anchor fires many events per second
        const timers = updateTimersRef.current
        if (timers.has(drawing.id)) clearTimeout(timers.get(drawing.id))
        const t = setTimeout(() => {
          timers.delete(drawing.id)
          _putUpdate(symbol, serverId, payload).catch(() => {})
          if (aliveRef.current) {
            setDrawings(prev => prev.map(d => (d.id === serverId ? { ...d, payload } : d)))
          }
        }, DEBOUNCE_MS)
        timers.set(drawing.id, t)
      })

      offRemoved = manager.on('drawing:removed', (evt) => {
        const libId = evt && evt.drawingId
        if (!libId) return
        const serverId = idMapRef.current.get(libId)
        idMapRef.current.delete(libId)
        if (serverId) {
          _delete(symbol, serverId).catch(() => {})
          if (aliveRef.current) setDrawings(prev => prev.filter(d => d.id !== serverId))
        }
      })

      offToolChanged = manager.on('tool:changed', (evt) => {
        if (!aliveRef.current) return
        setActiveTool(evt && evt.toolType ? evt.toolType : null)
      })

      // / gh issue #1920: primitives drift on pan/zoom — poke requestUpdate on visible range change
      try {
        const timeScale = chart.timeScale && chart.timeScale()
        if (timeScale && typeof timeScale.subscribeVisibleTimeRangeChange === 'function') {
          const handler = () => {
            const mgr = managerRef.current
            if (!mgr) return
            try {
              const all = typeof mgr.getAllDrawings === 'function' ? mgr.getAllDrawings() : []
              for (const d of all) {
                if (d && typeof d.requestUpdate === 'function') d.requestUpdate()
              }
            } catch { /* / disposed */ }
          }
          timeScale.subscribeVisibleTimeRangeChange(handler)
          unsubRange = () => {
            try { timeScale.unsubscribeVisibleTimeRangeChange(handler) } catch { /* / gone */ }
          }
        }
      } catch { /* / chart gone */ }

      // / hydrate existing drawings from the backend
      try {
        const resp = await fetch(`/api/drawings/${symbol}`)
        if (resp.ok && !cancelled && managerRef.current === manager) {
          const rows = await resp.json()
          if (Array.isArray(rows)) {
            const registry = typeof lib.getToolRegistry === 'function' ? lib.getToolRegistry() : null
            for (const row of rows) {
              const libType = DB_TO_TYPE[row.drawing_type]
              if (!libType || !registry) continue
              try {
                const anchors = (row.payload && Array.isArray(row.payload.anchors)) ? row.payload.anchors : []
                const style = (row.payload && row.payload.style) || {}
                const options = (row.payload && row.payload.options) || {}
                const clientId = _newClientId()
                const d = registry.createDrawing(libType, clientId, anchors, style, options)
                if (d) {
                  // / tag so drawing:added handler skips it
                  idMapRef.current.set(clientId, row.id)
                  if (typeof d.fromJSON === 'function' && row.payload) {
                    try { d.fromJSON({ ...row.payload, id: clientId, type: libType }) } catch { /* / bad payload */ }
                  }
                  manager.addDrawing(d)
                }
              } catch { /* / skip bad row */ }
            }
            if (aliveRef.current && managerRef.current === manager) {
              setDrawings(rows.map(r => ({ id: r.id, drawing_type: r.drawing_type, payload: r.payload, libId: null })))
            }
          }
        }
      } catch { /* / hydration failed, start empty */ }
    })()

    return () => {
      cancelled = true
      doDetach()
    }
  }, [chart, priceSeries, symbol])

  const setTool = useCallback((toolName) => {
    const mgr = managerRef.current
    if (!mgr || typeof mgr.setActiveTool !== 'function') {
      setActiveTool(toolName || null)
      return
    }
    try {
      mgr.setActiveTool(toolName || null)
      setActiveTool(toolName || null)
    } catch { /* / manager gone */ }
  }, [])

  const clear = useCallback(async () => {
    const mgr = managerRef.current
    const current = drawingsRef.current.slice()
    if (mgr && typeof mgr.clearAll === 'function') {
      try { mgr.clearAll() } catch { /* / disposed */ }
    }
    idMapRef.current.clear()
    setDrawings([])
    // / fire deletes in parallel; ignore errors individually
    await Promise.all(current.map(d => _delete(symbol, d.id).catch(() => {})))
  }, [symbol])

  const undo = useCallback(async () => {
    const current = drawingsRef.current
    if (current.length === 0) return
    const last = current[current.length - 1]
    const mgr = managerRef.current
    if (mgr && last.libId && typeof mgr.removeDrawing === 'function') {
      try { mgr.removeDrawing(last.libId) } catch { /* / gone */ }
    }
    if (last.libId) idMapRef.current.delete(last.libId)
    setDrawings(prev => prev.slice(0, -1))
    await _delete(symbol, last.id).catch(() => {})
  }, [symbol])

  return { drawings, activeTool, setTool, clear, undo }
}

// / context bridge: SymbolDetail wraps chart + toolbar in ChartToolsProvider; LWChart publishes
// / the live drawing controls so DrawingToolbar can drive the manager from outside.
// / publish is stable; we never put chartTools in LWChart's effect deps so the publish loop
// / never feeds back through render. activeTool is local state since the toolbar reads it.
const ChartToolsContext = createContext(null)

export function ChartToolsProvider({ children }) {
  const [activeTool, setActiveToolState] = useState(null)
  // / latch the live setTool/clear/undo references via state so consumers re-render when
  // / LWChart mounts the manager and again when it unmounts. publish is stable across renders.
  const [setToolImpl, setSetToolImpl] = useState(() => null)
  const [clearImpl, setClearImpl] = useState(() => null)
  const [undoImpl, setUndoImpl] = useState(() => null)

  const publish = useCallback((ctrl) => {
    // / functions stored via setState callback form to avoid being interpreted as updaters
    setSetToolImpl(() => (ctrl && typeof ctrl.setTool === 'function' ? ctrl.setTool : null))
    setClearImpl(() => (ctrl && typeof ctrl.clear === 'function' ? ctrl.clear : null))
    setUndoImpl(() => (ctrl && typeof ctrl.undo === 'function' ? ctrl.undo : null))
  }, [])

  const setTool = useCallback((toolName) => {
    setActiveToolState(toolName || null)
    if (typeof setToolImpl === 'function') setToolImpl(toolName)
  }, [setToolImpl])

  const clear = useCallback(async () => {
    if (typeof clearImpl === 'function') await clearImpl()
  }, [clearImpl])

  const undo = useCallback(async () => {
    if (typeof undoImpl === 'function') await undoImpl()
  }, [undoImpl])

  const syncActive = useCallback((tool) => {
    setActiveToolState(tool || null)
  }, [])

  const value = useMemo(
    () => ({ activeTool, setTool, clear, undo, publish, syncActive }),
    [activeTool, setTool, clear, undo, publish, syncActive],
  )

  return createElement(ChartToolsContext.Provider, { value }, children)
}

export function useChartTools() {
  return useContext(ChartToolsContext)
}

async function _postCreate(symbol, drawing_type, payload) {
  try {
    const resp = await fetch(`/api/drawings/${symbol}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ drawing_type, payload }),
    })
    if (!resp.ok) return null
    return await resp.json()
  } catch {
    return null
  }
}

async function _putUpdate(symbol, id, payload) {
  try {
    await fetch(`/api/drawings/${symbol}/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ payload }),
    })
  } catch { /* / swallow */ }
}

async function _delete(symbol, id) {
  try {
    await fetch(`/api/drawings/${symbol}/${id}`, { method: 'DELETE' })
  } catch { /* / swallow */ }
}
