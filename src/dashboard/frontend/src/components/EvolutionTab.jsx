import { useMemo } from 'react'
import Panel from './Panel'
import { SkeletonTable } from './Skeleton'

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
  // / track by strategy_id whether its spawn/mutate event was wiki-guided
  const strategyGuided = {}
  const strategyKilled = {}
  for (const e of events) {
    const id = e.strategy_id
    if (!id) continue
    const meta = e.metadata || e.details || {}
    const isGuided = meta.wiki_guided === true
    if (e.action === 'mutate' || e.action === 'spawn' || e.action === 'spawn_tier2') {
      // / earliest birth event wins if multiple
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
      <div className="text-text-muted text-sm py-2">
        No wiki_guided metadata in evolution events yet. Evolution engine may not be tagging mutations — flag for backend.
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
      <div className="bg-bg-primary border border-border p-3 border-l-2 border-l-accent">
        <div className="text-[10px] uppercase text-text-muted">Wiki-Guided</div>
        <div className="text-xl font-mono font-bold text-accent">
          {(gRate * 100).toFixed(0)}%
        </div>
        <div className="text-[10px] text-text-secondary">
          {g.survived} / {g.total} alive
        </div>
      </div>
      <div className="bg-bg-primary border border-border p-3">
        <div className="text-[10px] uppercase text-text-muted">Random Mutation</div>
        <div className="text-xl font-mono font-bold text-text-secondary">
          {(rRate * 100).toFixed(0)}%
        </div>
        <div className="text-[10px] text-text-secondary">
          {r.survived} / {r.total} alive
        </div>
      </div>
      <div className={`bg-bg-primary border border-border p-3 border-l-2 ${diff >= 0 ? 'border-l-profit' : 'border-l-loss'}`}>
        <div className="text-[10px] uppercase text-text-muted">Delta</div>
        <div className={`text-xl font-mono font-bold ${diff >= 0 ? 'text-profit' : 'text-loss'}`}>
          {diff >= 0 ? '+' : ''}{(diff * 100).toFixed(1)}pp
        </div>
        <div className="text-[10px] text-text-secondary">
          {diff > 0 ? 'wiki beats random' : diff < 0 ? 'random beats wiki' : 'no difference'}
        </div>
      </div>
    </div>
  )
}

export default function EvolutionTab({ evolution, loading }) {
  const genSurvived = useMemo(() => computeGenerationsSurvived(evolution || []), [evolution])
  const wikiStats = useMemo(() => computeWikiGuidedStats(evolution || []), [evolution])

  if (loading) {
    return <Panel title="Evolution Log"><SkeletonTable rows={6} cols={7} /></Panel>
  }

  return (
    <div className="space-y-2">
      <Panel title="Wiki-Guided A/B">
        <SurvivalStats stats={wikiStats} />
      </Panel>

      <Panel title="Evolution Log">
        {!evolution || evolution.length === 0 ? (
          <div className="text-text-muted text-sm py-8">
            Evolution engine hasn't run yet
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-secondary text-[11px] uppercase">
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
                    kill: 'text-loss',
                    mutate: 'text-accent',
                    spawn: 'text-accent',
                    spawn_tier2: 'text-accent',
                    promote: 'text-profit',
                    graduate: 'text-profit',
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
                      <td className="px-2 py-1 text-text-muted truncate max-w-[100px]">{e.parent_id || '--'}</td>
                      <td className="px-2 py-1 text-right font-mono text-text-secondary">
                        {gs ? (
                          <span className={gs.killed ? 'text-text-muted' : 'text-profit'}>
                            {gs.generations}
                          </span>
                        ) : '--'}
                      </td>
                      <td className="px-2 py-1">
                        {wikiGuided ? (
                          <span className="text-[10px] uppercase text-accent font-semibold">wiki</span>
                        ) : (
                          <span className="text-[10px] text-text-muted">rand</span>
                        )}
                      </td>
                      <td className="px-2 py-1 text-text-secondary truncate max-w-[200px]" title={e.reason}>{e.reason || '--'}</td>
                      <td className="px-2 py-1 text-right text-text-muted">{e.created_at?.replace('T', ' ').slice(0, 16) || '--'}</td>
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
