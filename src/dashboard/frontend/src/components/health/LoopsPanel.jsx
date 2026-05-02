import { useState, useCallback } from 'react'
import Card from '../ui/Card'
import Pill from '../ui/Pill'
import { useApi } from '../../hooks/useApi'

// / loop registry + trigger

function timeAgo(ts) {
  if (!ts) return 'never'
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 0) return 'just now'
  const m = Math.floor(diff / 60000)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function timeUntil(ts) {
  if (!ts) return '—'
  const diff = new Date(ts).getTime() - Date.now()
  if (diff < 0) return 'due'
  const m = Math.floor(diff / 60000)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

function cadence(loop) {
  if (loop.kind === 'cron') {
    const hh = loop.cron_hour_et
    return `cron ${hh < 12 ? `${hh || 12}am` : hh === 12 ? '12pm' : `${hh - 12}pm`} ET`
  }
  const s = loop.cadence_seconds || 0
  if (s >= 86400) return `${Math.floor(s / 86400)}d`
  if (s >= 3600) return `${Math.floor(s / 3600)}h`
  if (s >= 60) return `${Math.floor(s / 60)}m`
  return `${s}s`
}

function statusVariant(s) {
  if (s === 'ok') return 'live'
  if (s === 'error') return 'killed'
  if (s === 'running') return 'paper'
  return ''
}

export default function LoopsPanel() {
  const { data, loading, refetch } = useApi('/api/loops', 30000)
  const [triggering, setTriggering] = useState(() => new Set())
  const [triggerError, setTriggerError] = useState(null)

  const trigger = useCallback(async (name) => {
    setTriggerError(null)
    setTriggering((p) => new Set(p).add(name))
    const token = localStorage.getItem('qts-admin-token') || ''
    const headers = token ? { Authorization: `Bearer ${token}` } : {}
    try {
      const resp = await fetch(`/api/admin/trigger/${name}`, { method: 'POST', headers })
      if (resp.status === 401) {
        const entered = window.prompt('ADMIN_TOKEN required:', token)
        if (entered) {
          localStorage.setItem('qts-admin-token', entered)
          const retry = await fetch(`/api/admin/trigger/${name}`, {
            method: 'POST', headers: { Authorization: `Bearer ${entered}` },
          })
          if (!retry.ok) throw new Error(`${retry.status}`)
        } else throw new Error('unauthorized')
      } else if (!resp.ok) {
        throw new Error(`${resp.status}`)
      }
      setTimeout(() => {
        refetch()
        setTriggering((p) => { const n = new Set(p); n.delete(name); return n })
      }, 3000)
    } catch (err) {
      setTriggerError(`${name}: ${err.message}`)
      setTriggering((p) => { const n = new Set(p); n.delete(name); return n })
    }
  }, [refetch])

  const loops = data?.loops || []

  return (
    <Card title={<><b>loop registry</b></>} meta={`${loops.length} loops`} p0>
      {triggerError && (
        <div className="neg" style={{ padding: '8px 14px', fontSize: 11, borderBottom: '1px solid var(--line)' }}>
          trigger failed — {triggerError}
        </div>
      )}
      {loading && loops.length === 0 ? (
        <div className="empty-state"><div className="empty-state-title">loading loops</div></div>
      ) : loops.length === 0 ? (
        <div className="empty-state"><div className="empty-state-title">no loops registered</div></div>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>loop</th>
              <th>cadence</th>
              <th>last fire</th>
              <th>next in</th>
              <th>status</th>
              <th className="r">duration</th>
              <th className="r">action</th>
            </tr>
          </thead>
          <tbody>
            {loops.map((l) => (
              <tr key={l.name}>
                <td className="sym">{l.name}</td>
                <td className="dim">{cadence(l)}</td>
                <td className="dim">{timeAgo(l.last_fire_ts)}</td>
                <td className="dim">{timeUntil(l.next_fire_ts)}</td>
                <td>
                  <Pill variant={statusVariant(l.last_status)}>{l.last_status || 'pending'}</Pill>
                </td>
                <td className="r dim">{l.last_duration_ms != null ? `${l.last_duration_ms}ms` : '—'}</td>
                <td className="r">
                  <button
                    onClick={() => trigger(l.name)}
                    disabled={triggering.has(l.name) || l.last_status === 'running'}
                    style={{
                      fontSize: 10, padding: '2px 8px',
                      background: 'transparent', border: '1px solid var(--line)',
                      color: 'var(--ink-2)', cursor: 'pointer',
                      opacity: triggering.has(l.name) || l.last_status === 'running' ? 0.4 : 1,
                    }}
                  >
                    {triggering.has(l.name) ? 'queued' : 'run'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}
