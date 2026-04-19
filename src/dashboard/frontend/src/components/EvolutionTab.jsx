import { useMemo } from 'react'
import Panel from './Panel'
import { SkeletonTable } from './Skeleton'
import EmptyState from './EmptyState'
import HeroBanner from './HeroBanner'
import { useApi } from '../hooks/useApi'

// / walk the evolution log to compute how many generations each strategy has survived.
// / a strategy survives if its earliest event is from an older generation than its latest.
function computeGenerationsSurvived(events) {
  const byStrategy = {}
  for (const e of events) {
    const id = e.strategy_id
    if (!id) continue
    const gen = parseInt(e.generation) || 0
    if (!byStrategy[id]) byStrategy[id] = { min: gen, max: gen, killed: false }
    byStrategy[id].min = Math.min(byStrategy[id].min, gen)
    byStrategy[id].max = Math.max(byStrategy[id].max, gen)
    if (e.action === 'kill') byStrategy[id].killed = true
  }
  const result = {}
  for (const [id, v] of Object.entries(byStrategy)) {
    result[id] = { generations: v.max - v.min + 1, killed: v.killed }
  }
  return result
}

// / wiki-guided A/B breakdown: partition events into wiki_guided vs random groups
// / show survival rate (non-killed) per group
function computeWikiGuidedStats(events) {
  const groups = { wiki_guided: { total: 0, survived: 0 }, random: { total: 0, survived: 0 } }
  const strategyGuided = {}
  const strategyKilled = {}
  for (const e of events) {
    const id = e.strategy_id
    if (!id) continue
    const meta = e.metadata || e.details || {}
    const isGuided = meta.wiki_guided === true
    if (e.action === 'mutate' || e.action === 'spawn' || e.action === 'spawn_tier2') {
      if (!(id in strategyGuided)) strategyGuided[id] = isGuided
    }
    if (e.action === 'kill') strategyKilled[id] = true
  }
  for (const [id, guided] of Object.entries(strategyGuided)) {
    const bucket = guided ? 'wiki_guided' : 'random'
    groups[bucket].total++
    if (!strategyKilled[id]) groups[bucket].survived++
  }
  return groups
}

function SurvivalStats({ stats }) {
  const g = stats.wiki_guided
  const r = stats.random
  const gRate = g.total > 0 ? g.survived / g.total : 0
  const rRate = r.total > 0 ? r.survived / r.total : 0
  const diff = gRate - rRate
  const showEmpty = g.total === 0 && r.total === 0

  if (showEmpty) {
    return (
      <EmptyState
        title="No wiki_guided metadata yet"
        hint="Evolution events haven't been tagged wiki_guided vs random. First tagged mutation will populate this comparison."
      />
    )
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
      <div className="bg-bg-primary border border-border rounded p-3 border-l-2 border-l-accent">
        <div className="type-metric-label">Wiki-Guided</div>
        <div className="text-2xl font-mono font-bold text-accent mt-1">
          {(gRate * 100).toFixed(0)}%
        </div>
        <div className="text-[11px] text-text-secondary mt-1">
          {g.survived} / {g.total} alive
        </div>
      </div>
      <div className="bg-bg-primary border border-border rounded p-3">
        <div className="type-metric-label">Random Mutation</div>
        <div className="text-2xl font-mono font-bold text-text-secondary mt-1">
          {(rRate * 100).toFixed(0)}%
        </div>
        <div className="text-[11px] text-text-secondary mt-1">
          {r.survived} / {r.total} alive
        </div>
      </div>
      <div className={`bg-bg-primary border border-border rounded p-3 border-l-2 ${diff >= 0 ? 'border-l-profit' : 'border-l-loss'}`}>
        <div className="type-metric-label">Delta</div>
        <div className={`text-2xl font-mono font-bold mt-1 ${diff >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
          {diff >= 0 ? '+' : ''}{(diff * 100).toFixed(1)}pp
        </div>
        <div className="text-[11px] text-text-secondary mt-1">
          {diff > 0 ? 'wiki beats random' : diff < 0 ? 'random beats wiki' : 'no difference'}
        </div>
      </div>
    </div>
  )
}

// / time-ago helper — "2h ago" / "3d ago" / "never"
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

// / pull last evolution timestamp from the event stream
function lastEvolutionTs(events) {
  if (!Array.isArray(events) || events.length === 0) return null
  // / events are typically sorted desc by created_at, but don't assume
  let latest = null
  for (const e of events) {
    const t = e.created_at ? new Date(e.created_at).getTime() : 0
    if (t && (!latest || t > latest)) latest = t
  }
  return latest ? new Date(latest).toISOString() : null
}

function EvolutionHero({ strategies, events }) {
  const active = Array.isArray(strategies) ? strategies.filter(s => (s.status || '').toLowerCase() === 'active' || (s.status || '').toLowerCase() === 'live').length : 0
  const paper = Array.isArray(strategies) ? strategies.filter(s => (s.status || '').toLowerCase().startsWith('paper')).length : 0
  const total = Array.isArray(strategies) ? strategies.length : 0
  const lastTs = lastEvolutionTs(events)

  return (
    <HeroBanner>
      <div className="hero-metric">
        <span className="hero-metric-label">Active strategies</span>
        <span className="hero-metric-value font-mono pnl-positive">{active}</span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Paper trading</span>
        <span className="hero-metric-value font-mono text-accent">{paper}</span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Total tracked</span>
        <span className="hero-metric-value font-mono">{total}</span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Last evolution</span>
        <span className="hero-metric-value-sm font-mono text-text-primary">
          {timeAgo(lastTs)}
        </span>
      </div>
    </HeroBanner>
  )
}

export default function EvolutionTab({ evolution, loading }) {
  const genSurvived = useMemo(() => computeGenerationsSurvived(evolution || []), [evolution])
  const wikiStats = useMemo(() => computeWikiGuidedStats(evolution || []), [evolution])
  // / pull strategies separately so the hero counts stay accurate even when evolution log is empty
  const { data: strategies } = useApi('/api/strategies', 60000)

  return (
    <div className="space-y-6">
      <EvolutionHero strategies={strategies} events={evolution} />

      <Panel title="Wiki-Guided A/B">
        <SurvivalStats stats={wikiStats} />
      </Panel>

      <Panel title="Evolution Log">
        {loading ? (
          <SkeletonTable rows={6} cols={8} />
        ) : !evolution || evolution.length === 0 ? (
          <EmptyState
            title="No evolution events yet"
            hint="Evolution runs nightly at 2am. The engine kills bottom-25% strategies and mutates new configs from top performers + wiki context."
          />
        ) : (
          <div className="overflow-x-auto table-scroll-fade">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-secondary text-[11px] uppercase sticky top-0 bg-bg-surface">
                  <th className="text-left px-2 py-1">Gen</th>
                  <th className="text-left px-2 py-1">Action</th>
                  <th className="text-left px-2 py-1">Strategy</th>
                  <th className="text-left px-2 py-1">Parent</th>
                  <th className="text-right px-2 py-1">Gens Alive</th>
                  <th className="text-left px-2 py-1">Wiki</th>
                  <th className="text-left px-2 py-1">Reason</th>
                  <th className="text-right px-2 py-1">Date</th>
                </tr>
              </thead>
              <tbody>
                {evolution.map((e, i) => {
                  const actionColor = {
                    kill: 'pnl-negative',
                    mutate: 'text-accent',
                    spawn: 'text-accent',
                    spawn_tier2: 'text-accent',
                    promote: 'pnl-positive',
                    graduate: 'pnl-positive',
                  }[e.action] || 'text-text-secondary'
                  const gs = genSurvived[e.strategy_id]
                  const meta = e.metadata || e.details || {}
                  const wikiGuided = meta.wiki_guided === true
                  return (
                    <tr key={i} className="hover:bg-bg-hover border-t border-border" style={{ height: 36 }}>
                      <td className="px-2 py-1 font-mono">{e.generation}</td>
                      <td className={`px-2 py-1 font-semibold ${actionColor}`}>
                        {e.action?.toUpperCase()}
                      </td>
                      <td className="px-2 py-1 truncate max-w-[120px]" title={e.strategy_id}>{e.strategy_id}</td>
                      <td className="px-2 py-1 text-text-muted truncate max-w-[100px]">{e.parent_id || '—'}</td>
                      <td className="px-2 py-1 text-right font-mono text-text-secondary">
                        {gs ? (
                          <span className={gs.killed ? 'text-text-muted' : 'pnl-positive'}>
                            {gs.generations}
                          </span>
                        ) : '—'}
                      </td>
                      <td className="px-2 py-1">
                        {wikiGuided ? (
                          <span className="chip chip-accent">wiki</span>
                        ) : (
                          <span className="text-[10px] text-text-muted">rand</span>
                        )}
                      </td>
                      <td className="px-2 py-1 text-text-secondary truncate max-w-[200px]" title={e.reason}>{e.reason || '—'}</td>
                      <td className="px-2 py-1 text-right text-text-muted">{e.created_at?.replace('T', ' ').slice(0, 16) || '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}
