import { useMemo } from 'react'
import Card from '../ui/Card'
import KVList from '../ui/KVList'
import { useApi } from '../../hooks/useApi'

// / latest parent → child

function findStrategy(strategies, id) {
  if (!Array.isArray(strategies) || !id) return null
  return strategies.find((s) => s.strategy_id === id || s.id === id) || null
}

function statusTone(s) {
  if (!s) return ''
  const v = String(s).toLowerCase()
  if (v.includes('killed') || v === 'kill') return 'neg'
  if (v.includes('paper')) return 'acc'
  if (v.includes('active') || v.includes('live') || v.includes('promoted')) return 'pos'
  return 'dim'
}

function entries(strat) {
  if (!strat) return [{ k: 'status', v: <span className="dim">not found</span> }]
  return [
    { k: 'name', v: strat.name || '—' },
    { k: 'asset_class', v: strat.asset_class || '—' },
    { k: 'entry signals', v: strat.entry_conditions_count ?? '—' },
    { k: 'exit conditions', v: strat.exit_conditions_count ?? '—' },
    { k: 'sharpe', v: strat.sharpe_ratio != null ? Number(strat.sharpe_ratio).toFixed(2) : <span className="dim">—</span> },
    { k: 'win rate', v: strat.win_rate != null ? `${(strat.win_rate * 100).toFixed(0)}%` : <span className="dim">—</span> },
    { k: 'max drawdown', v: strat.max_drawdown != null ? `${(strat.max_drawdown * 100).toFixed(1)}%` : <span className="dim">—</span> },
    { k: 'brier', v: strat.brier_score != null ? Number(strat.brier_score).toFixed(3) : <span className="dim">—</span> },
    { k: 'status', v: <span className={statusTone(strat.status)}>{strat.status || 'pending'}</span> },
  ]
}

export default function ConfigDiffPanel({ events }) {
  const { data: strategies } = useApi('/api/strategies', 60000)

  const lineage = useMemo(() => {
    if (!Array.isArray(events)) return null
    const e = events.find((x) =>
      (x.action === 'mutate' || x.action === 'spawn' || x.action === 'spawn_tier2') && x.parent_id
    )
    return e ? { parentId: e.parent_id, childId: e.strategy_id, when: e.created_at, reason: e.reason } : null
  }, [events])

  if (!lineage) {
    return (
      <Card title={<><b>config diff</b></>} meta="latest mutation lineage">
        <div className="empty-state">
          <div className="empty-state-title">no mutation lineage yet</div>
          <div className="empty-state-hint">first mutate or spawn event will surface its parent → child here.</div>
        </div>
      </Card>
    )
  }

  const parent = findStrategy(strategies, lineage.parentId)
  const child = findStrategy(strategies, lineage.childId)

  return (
    <div className="grid c2">
      <Card
        title={<><b>parent</b> · {lineage.parentId}</>}
        meta={parent?.status || '—'}
      >
        <KVList entries={entries(parent)} />
      </Card>
      <Card
        title={<><b>child</b> · {lineage.childId}</>}
        meta={lineage.when ? lineage.when.replace('T', ' ').slice(5, 16) : '—'}
      >
        <KVList entries={entries(child)} />
        {lineage.reason && (
          <div className="dim" style={{ fontSize: 11, marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--line)' }}>
            mutation: {lineage.reason}
          </div>
        )}
      </Card>
    </div>
  )
}
