import { useState, useEffect } from 'react'
import { useApi } from '../../hooks/useApi'
import { useWebSocketContext } from '../../contexts/WebSocketContext'

const MARKETS = ['all', 'equity', 'crypto']

// / regime type → color for timeline bars
const REGIME_COLORS = {
  bull: '#00dc82',
  bear: '#ff4757',
  sideways: '#f59e0b',
  high_vol: '#3b82f6',
  range: '#f59e0b',
  trending: '#00dc82',
  volatile: '#ef4444',
}

// / horizontal timeline — one bar per day colored by regime, event markers on top
function RegimeTimeline({ market }) {
  const effectiveMarket = market === 'all' ? 'equity' : market
  const url = `/api/regime-timeline?market=${effectiveMarket}&days=90`
  const { data, loading, error } = useApi(url, 120000)

  if (loading && !data) return <div className="skeleton h-16 w-full mb-3" />
  if (error) return <div className="text-loss text-sm py-2">Failed to load timeline: {error}</div>
  if (!data || !Array.isArray(data.bars) || data.bars.length === 0) {
    return (
      <div className="text-text-muted text-sm py-2 mb-3">
        No timeline data for {effectiveMarket} — regime_history may be empty.
      </div>
    )
  }

  const bars = data.bars
  const events = Array.isArray(data.events) ? data.events : []

  // / build a quick lookup for tooltip info by date
  const eventsByDate = {}
  for (const e of events) {
    const day = (e.detected_at || '').split('T')[0]
    if (!day) continue
    if (!eventsByDate[day]) eventsByDate[day] = []
    eventsByDate[day].push(e)
  }

  // / unique regimes for legend
  const uniqueRegimes = [...new Set(bars.map(b => b.regime).filter(Boolean))]

  return (
    <div className="mb-4 space-y-2">
      <div className="text-[10px] uppercase text-text-secondary">
        {effectiveMarket} regime — last {bars.length} days
      </div>
      {/* timeline */}
      <div className="relative">
        <div className="flex h-8 w-full overflow-hidden border border-border rounded">
          {bars.map((b, i) => {
            const color = REGIME_COLORS[b.regime] || '#555570'
            const day = b.date?.split('T')[0] || ''
            const evts = eventsByDate[day] || []
            const title = [
              `${day}: ${b.regime || 'unknown'}`,
              ...evts.map(e => `${e.old_regime} → ${e.new_regime} (conf ${e.confidence != null ? Number(e.confidence).toFixed(2) : '—'})`),
            ].join('\n')
            return (
              <div
                key={i}
                className="flex-1 relative cursor-help"
                style={{ background: color, minWidth: 2 }}
                title={title}
              >
                {evts.length > 0 && (
                  <div
                    className="absolute top-0 left-1/2 -translate-x-1/2 w-1 h-full bg-text-primary"
                    style={{ boxShadow: '0 0 3px rgba(255,255,255,0.8)' }}
                  />
                )}
              </div>
            )
          })}
        </div>
      </div>
      {/* legend */}
      <div className="flex flex-wrap gap-3 text-[10px]">
        {uniqueRegimes.map(r => (
          <span key={r} className="flex items-center gap-1 text-text-secondary">
            <span
              className="w-3 h-3 inline-block border border-border"
              style={{ background: REGIME_COLORS[r] || '#555570' }}
            />
            {r}
          </span>
        ))}
        {events.length > 0 && (
          <span className="flex items-center gap-1 text-text-secondary ml-auto">
            <span className="w-0.5 h-3 inline-block bg-text-primary" />
            {events.length} shifts
          </span>
        )}
      </div>
    </div>
  )
}

export default function RegimeShiftList() {
  const [market, setMarket] = useState('all')
  const url = market === 'all'
    ? '/api/regime-shifts?limit=100'
    : `/api/regime-shifts?market=${market}&limit=100`
  const { data, loading, refetch } = useApi(url, 60000)
  const { subscribe } = useWebSocketContext()

  // / live refresh when a regime_shift event fires
  useEffect(() => {
    const unsub = subscribe('regime_shift', () => refetch())
    return unsub
  }, [subscribe, refetch])

  return (
    <>
      <div className="flex gap-1 mb-3 text-xs">
        {MARKETS.map((m) => (
          <button
            key={m}
            onClick={() => setMarket(m)}
            className={`px-2 py-1 rounded border
              ${market === m
                ? 'bg-accent text-bg-primary border-accent'
                : 'bg-bg-primary text-text-secondary border-border hover:text-text-primary'
              }`}
          >
            {m}
          </button>
        ))}
      </div>

      <RegimeTimeline market={market} />

      {loading && <div className="text-text-muted text-sm py-8">loading…</div>}
      {!loading && (!data || data.length === 0) && (
        <div className="text-text-muted text-sm py-8">No regime shifts recorded yet.</div>
      )}
      {!loading && data && data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-secondary text-[11px] uppercase">
                <th className="text-left px-2 py-1">Detected</th>
                <th className="text-left px-2 py-1">Market</th>
                <th className="text-left px-2 py-1">Transition</th>
                <th className="text-right px-2 py-1">Confidence</th>
                <th className="text-left px-2 py-1">Wiki</th>
              </tr>
            </thead>
            <tbody>
              {data.map((rs) => (
                <tr key={rs.id} className="border-b border-border/50 hover:bg-bg-primary/50">
                  <td className="px-2 py-1.5 text-text-muted">
                    {rs.detected_at ? new Date(rs.detected_at).toLocaleString() : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-text-secondary">{rs.market}</td>
                  <td className="px-2 py-1.5">
                    <span className="font-mono text-text-muted">{rs.old_regime}</span>
                    <span className="mx-1 text-text-muted">→</span>
                    <span className="font-mono text-accent">{rs.new_regime}</span>
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-text-muted">
                    {rs.confidence != null ? Number(rs.confidence).toFixed(2) : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-text-muted font-mono truncate max-w-[220px]">
                    {rs.wiki_path || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
