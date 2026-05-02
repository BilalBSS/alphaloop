import Card from '../ui/Card'
import Pill from '../ui/Pill'
import { useApi } from '../../hooks/useApi'

// / wiki neighbors fed to mutator

export default function WikiRetrievalPanel({ cycleId }) {
  const url = cycleId
    ? `/api/wiki/retrieval/${encodeURIComponent(cycleId)}`
    : '/api/wiki/retrieval/latest'
  const { data, loading, error } = useApi(url, null)

  return (
    <Card
      title={<><b>wiki retrieval</b></>}
      meta={
        cycleId
          ? <code style={{ fontSize: 10 }}>{cycleId.slice(0, 18)}</code>
          : <Pill variant="new">latest</Pill>
      }
      p0
    >
      <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: '0.06em' }}>
        nearest neighbors fed to mutator
      </div>
      {loading && <div className="empty-state" style={{ padding: '32px 14px' }}>
        <div className="empty-state-title">loading…</div>
      </div>}
      {!loading && error && <div className="empty-state" style={{ padding: '32px 14px' }}>
        <div className="empty-state-title">no retrieval log yet</div>
        <div className="empty-state-hint">click a wiki-guided mutation row above.</div>
      </div>}
      {!loading && !error && data && (
        <RetrievalBody data={data} />
      )}
      <div style={{ padding: '10px 14px', fontSize: 10.5, color: 'var(--ink-4)', fontFamily: 'var(--mono)', lineHeight: 1.6, borderTop: '1px solid var(--line)' }}>
        prompt tokens · {data?.prompt_tokens ?? 0}
      </div>
    </Card>
  )
}

function RetrievalBody({ data }) {
  const retrieved = data?.retrieved
  const ctx = retrieved?.context
  if (!ctx) {
    return (
      <div className="empty-state" style={{ padding: '32px 14px' }}>
        <div className="empty-state-title">no neighbors yet</div>
      </div>
    )
  }
  const lines = ctx.split('\n').filter((l) => l.trim()).slice(0, 12)
  return (
    <div style={{ padding: '10px 14px', fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.55, maxHeight: 240, overflowY: 'auto' }}>
      {lines.map((l, i) => (
        <div key={i} style={{ marginBottom: 4 }}>{l}</div>
      ))}
    </div>
  )
}
