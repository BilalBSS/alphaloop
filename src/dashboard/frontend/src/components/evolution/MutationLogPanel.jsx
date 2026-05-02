import Card from '../ui/Card'

// / .muts log rows

function statusKind(action) {
  switch (action) {
    case 'kill':        return 'kill'
    case 'promote':
    case 'graduate':    return 'promote'
    case 'mutate':
    case 'spawn':
    case 'spawn_tier2': return 'paper'
    case 'discard':     return 'discard'
    default:            return ''
  }
}

function fmtTs(ts) {
  if (!ts) return '—'
  return ts.replace('T', ' ').slice(5, 16)
}

function highlightStrategies(text) {
  if (!text) return null
  const parts = String(text).split(/(strategy_\d+\w?)/g)
  return parts.map((p, i) =>
    /^strategy_\d+/.test(p) ? <b key={i}>{p}</b> : <span key={i}>{p}</span>
  )
}

export default function MutationLogPanel({ events, loading }) {
  if (loading) {
    return (
      <Card title={<><b>mutation</b> log</>} meta="evolution_log" p0>
        <div className="empty-state"><div className="empty-state-title">loading mutations</div></div>
      </Card>
    )
  }
  if (!events || events.length === 0) {
    return (
      <Card title={<><b>mutation</b> log</>} meta="evolution_log" p0>
        <div className="empty-state">
          <div className="empty-state-title">no evolution events yet</div>
          <div className="empty-state-hint">cron 02:30 ET nightly · kills bottom 25% then mutates new configs.</div>
        </div>
      </Card>
    )
  }

  return (
    <Card title={<><b>mutation</b> log</>} meta="deepseek-v3 mutator" p0>
      <div className="muts">
        {events.slice(0, 30).map((e, i) => {
          const kind = statusKind(e.action)
          const meta = e.metadata || e.details || {}
          const wiki = meta.wiki_guided === true
          const wfSharpe = meta.wf_sharpe ?? meta.walk_forward_sharpe ?? meta.sharpe ?? null
          const desc = e.reason || `${e.action} ${e.strategy_id}${e.parent_id ? ` (← ${e.parent_id})` : ''}`
          return (
            <div key={i} className="row">
              <span className="ts">{fmtTs(e.created_at)}</span>
              <span className={`k ${kind}`}>{e.action || '—'}</span>
              <span className="txt">
                {highlightStrategies(desc)}
                {wiki && <span className="k wiki" style={{ marginLeft: 6 }}>wiki</span>}
              </span>
              <span className="wf">{wfSharpe != null ? `wf ${Number(wfSharpe).toFixed(2)}` : '—'}</span>
            </div>
          )
        })}
      </div>
    </Card>
  )
}
