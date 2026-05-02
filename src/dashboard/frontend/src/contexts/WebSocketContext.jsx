import { createContext, useContext, useEffect, useRef, useCallback, useMemo } from 'react'
import { useWebSocket } from '../hooks/useApi'

// / context exposes { subscribe(eventType, callback), status }
// / subscribers are called when backend broadcasts an event matching their type
// / events: 'position_update', 'trade_executed', 'signal_generated', 'regime_shift',
// / 'strategy_status_change', 'decision_made', 'gate_breach'
const WebSocketContext = createContext(null)

export function WebSocketProvider({ children }) {
  const { status, lastMessage } = useWebSocket()
  // / subscribers keyed by event type, each value is a Set of callbacks
  const subscribersRef = useRef(new Map())

  // / dispatch lastMessage to any subscribers registered for its type
  useEffect(() => {
    if (!lastMessage || typeof lastMessage !== 'object') return
    const type = lastMessage.type
    if (!type) return
    const subs = subscribersRef.current.get(type)
    if (!subs || subs.size === 0) return
    for (const cb of subs) {
      try {
        cb(lastMessage)
      } catch (err) {
        // / swallow subscriber errors so one bad callback doesn't break others
        console.error(`[WebSocketContext] subscriber error for ${type}:`, err)
      }
    }
  }, [lastMessage])

  // / stable subscribe reference — subscriber changes go into the ref-backed map
  const subscribe = useCallback((eventType, callback) => {
    const subs = subscribersRef.current
    if (!subs.has(eventType)) subs.set(eventType, new Set())
    subs.get(eventType).add(callback)
    return () => {
      const set = subs.get(eventType)
      if (set) {
        set.delete(callback)
        if (set.size === 0) subs.delete(eventType)
      }
    }
  }, [])

  const value = useMemo(() => ({ status, lastMessage, subscribe }), [status, lastMessage, subscribe])

  return (
    <WebSocketContext.Provider value={value}>
      {children}
    </WebSocketContext.Provider>
  )
}

// / stable no-op shape for callers used outside a provider
const NOOP_CTX = {
  status: 'disconnected',
  lastMessage: null,
  subscribe: () => () => {},
}

export function useWebSocketContext() {
  const ctx = useContext(WebSocketContext)
  return ctx || NOOP_CTX
}
