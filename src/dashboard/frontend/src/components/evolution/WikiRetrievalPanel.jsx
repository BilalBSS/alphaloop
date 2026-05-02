import Card from '../ui/Card'
import Pill from '../ui/Pill'

// / placeholder until C5

export default function WikiRetrievalPanel() {
  return (
    <Card
      title={<><b>wiki retrieval</b></>}
      meta={<Pill variant="new">pending C5</Pill>}
      p0
    >
      <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: '0.06em' }}>
        nearest neighbors fed to mutator
      </div>
      <div className="empty-state" style={{ padding: '32px 14px' }}>
        <div className="empty-state-title">no retrieval log yet</div>
        <div className="empty-state-hint">cosine sim per neighbor lands with retrieval_logs.</div>
      </div>
      <div style={{ padding: '10px 14px', fontSize: 10.5, color: 'var(--ink-4)', fontFamily: 'var(--mono)', lineHeight: 1.6, borderTop: '1px solid var(--line)' }}>
        target <code>/api/wiki/retrieval/:cycle_id</code>
      </div>
    </Card>
  )
}
