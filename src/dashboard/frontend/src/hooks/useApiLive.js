import { useEffect } from 'react'
import { useApi } from './useApi'
import { useWebSocketContext } from '../contexts/WebSocketContext'

// / useApi variant that also subscribes to WebSocket events and triggers refetch.
// / pass `invalidateOn: ['trade_executed', ...]` to re-fetch when those events arrive.
// / safe outside a WebSocketProvider — subscribe becomes a no-op.
export function useApiLive(url, interval = null, invalidateOn = []) {
  const result = useApi(url, interval)
  const { subscribe } = useWebSocketContext()

  // / serialize invalidateOn so identity changes don't re-subscribe every render
  const key = Array.isArray(invalidateOn) ? invalidateOn.join('|') : ''

  useEffect(() => {
    if (!invalidateOn || invalidateOn.length === 0) return
    const unsubs = invalidateOn.map(type => subscribe(type, () => result.refetch()))
    return () => unsubs.forEach(u => u && u())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscribe, key, result.refetch])

  return result
}
