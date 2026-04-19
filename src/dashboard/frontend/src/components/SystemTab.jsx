import { useState, useEffect, useCallback } from 'react'
import Panel from './Panel'
import HeroBanner from './HeroBanner'
import EmptyState from './EmptyState'
import { useApi } from '../hooks/useApi'

// / human-readable time-ago and time-until formatters
function timeAgo(ts) {
  if (!ts) return 'never'
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 0) return 'just now'
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${m % 60}m ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

function timeUntil(ts) {
  if (!ts) return '—'
  const diff = new Date(ts).getTime() - Date.now()
  if (diff < 0) return 'due'
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${m % 60}m`
  const d = Math.floor(h / 24)
  return `${d}d ${h % 24}h`
}

function cadenceLabel(loop) {
  if (loop.kind === 'cron') {
    const hour = loop.cron_hour_et
    const hh = hour === 0 ? '12am' : hour < 12 ? `${hour}am` : hour === 12 ? '12pm' : `${hour - 12}pm`
    return `cron ${hh} ET`
  }
  const s = loop.cadence_seconds || 0
  if (s >= 86400) return `${Math.floor(s / 86400)}d`
  if (s >= 3600) return `${Math.floor(s / 3600)}h`
  if (s >= 60) return `${Math.floor(s / 60)}m`
  return `${s}s`
}

function statusPill(status) {
  if (status === 'ok') {
    return <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-profit/20 pnl-positive font-semibold">ok</span>
  }
  if (status === 'running') {
    return <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-warning/20 text-warning font-semibold">running</span>
  }
  if (status === 'error') {
    return <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-loss/20 pnl-negative font-semibold">error</span>
  }
  return <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-bg-primary text-text-muted font-semibold">pending</span>
}

// / env-var presence pills — green=set, red=missing
function EnvHealthGrid({ envHealth }) {
  if (!envHealth) return null
  const { required = {}, optional = {} } = envHealth
  const renderPill = (key, set) => (
    <div
      key={key}
      className={`flex items-center gap-1.5 px-2 py-1 rounded text-[11px] border ${
        set ? 'bg-profit/10 border-profit/40 pnl-positive' : 'bg-loss/10 border-loss/40 pnl-negative'
      }`}
      title={set ? 'env var set' : 'env var missing'}
    >
      <div className={`w-1.5 h-1.5 rounded-full ${set ? 'bg-profit' : 'bg-loss'}`} />
      <span className="font-mono">{key}</span>
    </div>
  )
  const missingRequired = Object.entries(required).filter(([, v]) => !v)
  return (
    <Panel title="Environment health">
      {missingRequired.length > 0 && (
        <div className="pnl-negative text-xs border-l-2 border-loss pl-3 py-1 mb-3">
          {missingRequired.length} required env var{missingRequired.length === 1 ? '' : 's'} missing — some loops will silently skip
        </div>
      )}
      <div className="mb-3">
        <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1.5">Required</div>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(required).map(([k, v]) => renderPill(k, v))}
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1.5">Optional</div>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(optional).map(([k, v]) => renderPill(k, v))}
        </div>
      </div>
    </Panel>
  )
}

// / handbuilt vs alpha158 brier + IC comparison on demand; cached an hour server-side.
function FeatureBenchmarkPanel() {
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)

  const run = async (sym = 'SPY') => {
    setLoading(true)
    setErr(null)
    try {
      const resp = await fetch(`/api/feature-benchmark?symbol=${encodeURIComponent(sym)}`)
      if (!resp.ok) throw new Error(`${resp.status}`)
      setResult(await resp.json())
    } catch (e) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }

  const row = (label, obj) => {
    if (!obj) return null
    if (obj.error) {
      return (
        <tr>
          <td className="px-2 py-1 font-mono">{label}</td>
          <td colSpan={3} className="px-2 py-1 text-text-muted">{obj.error}</td>
        </tr>
      )
    }
    return (
      <tr>
        <td className="px-2 py-1 font-mono">{label}</td>
        <td className="px-2 py-1 font-mono text-right">{obj.brier}</td>
        <td className="px-2 py-1 font-mono text-right">{obj.ic ?? '—'}</td>
        <td className="px-2 py-1 font-mono text-right text-text-muted">{obj.feature_count}</td>
      </tr>
    )
  }

  const winner = result?.winner

  return (
    <Panel title="Feature-set benchmark (handbuilt vs alpha158)">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] text-text-muted">
          {result?.symbol ? `symbol: ${result.symbol}` : 'Runs a fast LightGBM A/B on 5y daily bars. Cached 1h.'}
        </div>
        <div className="flex gap-1.5">
          <button
            onClick={() => run('SPY')}
            disabled={loading}
            className="text-xs px-2 py-0.5 rounded border border-border bg-bg-primary hover:bg-accent/20 hover:border-accent disabled:opacity-40"
          >
            {loading ? 'running…' : 'Run SPY'}
          </button>
          <button
            onClick={() => run('AAPL')}
            disabled={loading}
            className="text-xs px-2 py-0.5 rounded border border-border bg-bg-primary hover:bg-accent/20 hover:border-accent disabled:opacity-40"
          >
            Run AAPL
          </button>
        </div>
      </div>
      {err && <div className="pnl-negative text-xs border-l-2 border-loss pl-3 py-1 mb-2">{err}</div>}
      {result?.error && (
        <div className="text-xs text-text-muted">benchmark unavailable — {result.error}</div>
      )}
      {result && !result.error && (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-text-muted border-b border-border">
              <th className="text-left px-2 py-1">Feature set</th>
              <th className="text-right px-2 py-1">Brier (↓)</th>
              <th className="text-right px-2 py-1">IC (↑)</th>
              <th className="text-right px-2 py-1">Features</th>
            </tr>
          </thead>
          <tbody>
            {row('handbuilt', result.handbuilt)}
            {row('alpha158', result.alpha158)}
          </tbody>
        </table>
      )}
      {winner && (
        <div className="text-xs mt-2">
          <span className="text-text-muted">winner: </span>
          <span className="pnl-positive font-mono">{winner}</span>
          <span className="text-text-muted"> (lower brier)</span>
        </div>
      )}
    </Panel>
  )
}

function LoopTable({ loops, onTrigger, triggering }) {
  if (!loops || loops.length === 0) {
    return <EmptyState title="No loops registered yet" />
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[10px] uppercase tracking-wider text-text-muted border-b border-border">
            <th className="text-left py-2 px-2">Loop</th>
            <th className="text-left py-2 px-2">Cadence</th>
            <th className="text-left py-2 px-2">Last fire</th>
            <th className="text-left py-2 px-2">Next fire in</th>
            <th className="text-left py-2 px-2">Status</th>
            <th className="text-left py-2 px-2">Duration</th>
            <th className="text-left py-2 px-2">Last error</th>
            <th className="text-right py-2 px-2">Actions</th>
          </tr>
        </thead>
        <tbody>
          {loops.map((loop) => (
            <tr key={loop.name} className="border-b border-border/50 hover:bg-bg-primary/50">
              <td className="py-1.5 px-2 font-mono text-xs">
                <div>{loop.name}</div>
                {loop.description && (
                  <div className="text-[10px] text-text-muted">{loop.description}</div>
                )}
              </td>
              <td className="py-1.5 px-2 text-text-secondary text-xs">{cadenceLabel(loop)}</td>
              <td className="py-1.5 px-2 text-text-secondary text-xs">{timeAgo(loop.last_fire_ts)}</td>
              <td className="py-1.5 px-2 text-text-secondary text-xs font-mono">{timeUntil(loop.next_fire_ts)}</td>
              <td className="py-1.5 px-2">{statusPill(loop.last_status)}</td>
              <td className="py-1.5 px-2 text-text-muted text-xs font-mono">
                {loop.last_duration_ms != null ? `${loop.last_duration_ms}ms` : '—'}
              </td>
              <td className="py-1.5 px-2 text-[10px] pnl-negative max-w-xs truncate" title={loop.last_error || ''}>
                {loop.last_error || ''}
              </td>
              <td className="py-1.5 px-2 text-right">
                <button
                  onClick={() => onTrigger(loop.name)}
                  disabled={triggering.has(loop.name) || loop.last_status === 'running'}
                  className="text-xs px-2 py-0.5 rounded border border-border bg-bg-primary hover:bg-accent/20 hover:border-accent disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {triggering.has(loop.name) ? 'queued' : 'Run now'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function SystemTab() {
  const { data: loopsData, loading: loopsLoading, refetch: refetchLoops } = useApi('/api/loops', 15000)
  const { data: envHealth, loading: envLoading } = useApi('/api/env-health', 60000)
  const [triggering, setTriggering] = useState(() => new Set())
  const [triggerError, setTriggerError] = useState(null)

  const trigger = useCallback(async (service) => {
    setTriggerError(null)
    setTriggering((prev) => new Set(prev).add(service))
    // / include admin token from localStorage when the server requires one
    const token = localStorage.getItem('qts-admin-token') || ''
    const headers = token ? { Authorization: `Bearer ${token}` } : {}
    try {
      const resp = await fetch(`/api/admin/trigger/${service}`, { method: 'POST', headers })
      if (resp.status === 401) {
        const entered = window.prompt(
          'ADMIN_TOKEN required — paste it (stored in this browser only, not sent anywhere else):',
          token,
        )
        if (entered) {
          localStorage.setItem('qts-admin-token', entered)
          const retry = await fetch(`/api/admin/trigger/${service}`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${entered}` },
          })
          if (!retry.ok) throw new Error(`${retry.status}`)
        } else {
          throw new Error('unauthorized')
        }
      } else if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        throw new Error(body.error || `${resp.status}`)
      }
      // / refetch loops shortly after the queue should have cleared
      setTimeout(() => {
        refetchLoops()
        setTriggering((prev) => {
          const next = new Set(prev)
          next.delete(service)
          return next
        })
      }, 3000)
    } catch (err) {
      setTriggerError(`${service}: ${err.message}`)
      setTriggering((prev) => {
        const next = new Set(prev)
        next.delete(service)
        return next
      })
    }
  }, [refetchLoops])

  const loops = loopsData?.loops || []
  const okCount = loops.filter((l) => l.last_status === 'ok').length
  const errorCount = loops.filter((l) => l.last_status === 'error').length
  const pendingCount = loops.filter((l) => !l.last_status).length

  return (
    <div className="space-y-4">
      <HeroBanner>
        <div className="hero-metric">
          <span className="hero-metric-label">Total loops</span>
          <span className="hero-metric-value font-mono">{loops.length}</span>
        </div>
        <div className="hero-metric">
          <span className="hero-metric-label">Healthy</span>
          <span className="hero-metric-value font-mono pnl-positive">{okCount}</span>
        </div>
        <div className="hero-metric">
          <span className="hero-metric-label">Errored</span>
          <span className={`hero-metric-value font-mono ${errorCount ? 'pnl-negative' : 'text-text-muted'}`}>
            {errorCount}
          </span>
        </div>
        <div className="hero-metric">
          <span className="hero-metric-label">Never fired</span>
          <span className={`hero-metric-value font-mono ${pendingCount ? 'text-warning' : 'text-text-muted'}`}>
            {pendingCount}
          </span>
        </div>
      </HeroBanner>

      {triggerError && (
        <div className="pnl-negative text-xs border-l-2 border-loss pl-3 py-2">
          Trigger failed — {triggerError}
        </div>
      )}

      <EnvHealthGrid envHealth={envHealth} />

      <FeatureBenchmarkPanel />

      <Panel title="Loop activity">
        {loopsLoading && !loops.length ? (
          <EmptyState title="Loading loops…" />
        ) : (
          <LoopTable loops={loops} onTrigger={trigger} triggering={triggering} />
        )}
      </Panel>
    </div>
  )
}
