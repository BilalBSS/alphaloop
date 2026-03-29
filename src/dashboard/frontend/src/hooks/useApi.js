import { useState, useEffect, useCallback } from 'react'

export function useApi(url, interval = null) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = useCallback(async () => {
    try {
      const resp = await fetch(url)
      if (!resp.ok) throw new Error(`${resp.status}`)
      const json = await resp.json()
      setData(json)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [url])

  useEffect(() => {
    fetchData()
    if (interval) {
      const id = setInterval(fetchData, interval)
      return () => clearInterval(id)
    }
  }, [fetchData, interval])

  return { data, loading, error, refetch: fetchData }
}

export function useWebSocket(url) {
  const [status, setStatus] = useState('connecting')
  const [lastMessage, setLastMessage] = useState(null)

  useEffect(() => {
    let ws
    let retryDelay = 1000

    function connect() {
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const wsUrl = url || `${protocol}://${window.location.host}/ws`
      ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        setStatus('connected')
        retryDelay = 1000
      }
      ws.onmessage = (e) => {
        try {
          setLastMessage(JSON.parse(e.data))
        } catch {}
      }
      ws.onclose = () => {
        setStatus('reconnecting')
        setTimeout(connect, retryDelay)
        retryDelay = Math.min(retryDelay * 2, 30000)
      }
      ws.onerror = () => ws.close()
    }

    connect()
    return () => ws?.close()
  }, [url])

  return { status, lastMessage }
}
