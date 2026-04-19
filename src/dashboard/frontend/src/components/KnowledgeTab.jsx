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
