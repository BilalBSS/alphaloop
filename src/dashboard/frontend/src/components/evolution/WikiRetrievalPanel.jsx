import Card from '../ui/Card'
import Pill from '../ui/Pill'
import { renderMarkdown } from '../ui/markdown'
import { useApi } from '../../hooks/useApi'

// / wiki neighbors fed to mutator

function fmtTs(ts) {
  if (!ts) return null
  return ts.replace('T', ' ').slice(0, 19)
}

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
      <RetrievalHeader data={data} />
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

function RetrievalHeader({ data }) {
  const ts = fmtTs(data?.ts)
  const parent = data?.parent_id
  const child = data?.child_id
  const items = []
  if (parent) items.push(`parent ${parent}`)
  if (child) items.push(`child ${child}`)
  return (
    <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: '0.06em' }}>
      <div>nearest neighbors fed to mutator</div>
      {(items.length > 0 || ts) && (
        <div style={{ marginTop: 4, color: 'var(--ink-4)' }}>
          {items.join(' · ')}{items.length > 0 && ts ? ' · ' : ''}{ts || ''}
        </div>
      )}
    </div>
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
  return (
    <div style={{ padding: '12px 16px' }}>
      {renderMarkdown(ctx)}
    </div>
  )
}
