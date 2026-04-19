import { useState, useMemo } from 'react'
import Panel from './Panel'
import HeroBanner from './HeroBanner'
import WikiBrowser from './knowledge/WikiBrowser'
import PostMortemList from './knowledge/PostMortemList'
import RegimeShiftList from './knowledge/RegimeShiftList'
import { useApi } from '../hooks/useApi'

const SUB_TABS = ['Wiki', 'Post-Mortems', 'Regime Shifts']

// / relative time — "2h ago" / "1d ago" / "never"
function timeAgo(ts) {
  if (!ts) return 'never'
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 0) return 'just now'
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

function timeUntil(ts) {
  if (!ts) return '—'
  const diff = new Date(ts).getTime() - Date.now()
  if (diff < 0) return 'due'
  const h = Math.floor(diff / 3600000)
  const m = Math.floor((diff % 3600000) / 60000)
  if (h < 24) return `${h}h ${m}m`
  const d = Math.floor(h / 24)
  return `${d}d ${h % 24}h`
}

function HydrationPanel() {
  const { data } = useApi('/api/hydration-status', 60000)
  if (!data) return null
  const { daily_cap = 5, hydrated_today = 0, next_fire_ts, last_event_ts, last_status } = data
  const pct = daily_cap > 0 ? Math.min(100, (hydrated_today / daily_cap) * 100) : 0
  const barColor = pct >= 100 ? 'bg-profit' : pct > 0 ? 'bg-accent' : 'bg-border'
  return (
    <Panel title="Symbol wiki hydration">
      <div className="space-y-3">
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm font-mono text-text-primary">
              {hydrated_today} / {daily_cap}
              <span className="text-xs text-text-muted ml-1">hydrated today</span>
            </span>
            <span className="text-[10px] uppercase tracking-wider text-text-muted">
              next run in {timeUntil(next_fire_ts)}
            </span>
          </div>
          <div className="w-full bg-bg-primary rounded-full h-1.5 overflow-hidden">
            <div
              className={`h-full ${barColor} transition-all`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
        <div className="flex items-center gap-4 text-[11px] text-text-muted">
          <div>
            last fire: <span className="text-text-secondary">{timeAgo(last_event_ts)}</span>
          </div>
          <div>
            status: <span className={
              last_status === 'ok' ? 'pnl-positive'
              : last_status === 'error' ? 'pnl-negative'
              : 'text-text-secondary'
            }>{last_status || 'pending'}</span>
          </div>
        </div>
        <div className="text-[11px] text-text-muted">
          Cap is env-configurable via <span className="font-mono">WIKI_HYDRATION_DAILY_CAP</span> (default 5).
          Bump to 25 for faster first-day fills.
        </div>
      </div>
    </Panel>
  )
}

function confidenceRank(raw) {
  if (!raw) return 0
  const s = String(raw).toLowerCase()
  if (s.includes('estab') || s.includes('high')) return 3
  if (s.includes('emerg') || s.includes('medium') || s === 'mid') return 2
  return 1
}

// / hero — established doc count + most recent update timestamp
function KnowledgeHero() {
  const { data: docs } = useApi('/api/wiki/documents?limit=500', 120000)

  const stats = useMemo(() => {
    if (!Array.isArray(docs)) return { total: 0, established: 0, lastUpdate: null }
    let established = 0
    let lastUpdate = null
    for (const d of docs) {
      if (confidenceRank(d.confidence) >= 3) established++
      const t = d.updated_at || d.modified_at || d.created_at
      if (t) {
        const ms = new Date(t).getTime()
        if (!lastUpdate || ms > lastUpdate) lastUpdate = ms
      }
    }
    return { total: docs.length, established, lastUpdate }
  }, [docs])

  return (
    <HeroBanner>
      <div className="hero-metric">
        <span className="hero-metric-label">Established docs</span>
        <span className="hero-metric-value font-mono pnl-positive">
          {stats.established}<span className="text-text-muted text-sm font-normal"> / {stats.total || '—'}</span>
        </span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Total docs</span>
        <span className="hero-metric-value font-mono">{stats.total || '—'}</span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Most recent update</span>
        <span className="hero-metric-value-sm font-mono">
          {timeAgo(stats.lastUpdate ? new Date(stats.lastUpdate).toISOString() : null)}
        </span>
      </div>
    </HeroBanner>
  )
}

export default function KnowledgeTab() {
  const [sub, setSub] = useState('Wiki')

  return (
    <div className="space-y-6">
      <KnowledgeHero />
      <HydrationPanel />
      <Panel title="Knowledge Base">
        <div className="flex gap-2 mb-4 text-xs">
          {SUB_TABS.map((t) => (
            <button
              key={t}
              onClick={() => setSub(t)}
              className={`px-3 py-1.5 rounded border transition-colors
                ${sub === t
                  ? 'bg-accent text-bg-primary border-accent'
                  : 'bg-bg-primary text-text-secondary border-border hover:text-text-primary'
                }`}
            >
              {t}
            </button>
          ))}
        </div>

        {sub === 'Wiki' && <WikiBrowser />}
        {sub === 'Post-Mortems' && <PostMortemList />}
        {sub === 'Regime Shifts' && <RegimeShiftList />}
      </Panel>
    </div>
  )
}
