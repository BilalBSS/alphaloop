import Card from '../ui/Card'
import Pill from '../ui/Pill'
import { useApi } from '../../hooks/useApi'

// / env var presence grid

function sortMissingFirst(entries) {
  return [...entries].sort(([ak, av], [bk, bv]) => (av === bv ? ak.localeCompare(bk) : av ? 1 : -1))
}

function VarPill({ name, set }) {
  const tone = set ? 'pos' : 'neg'
  return (
    <span
      className={tone}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '4px 8px', border: '1px solid var(--line)',
        fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.04em',
      }}
      title={set ? 'env var set' : 'missing — dependent loops will skip'}
    >
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: set ? 'var(--pos)' : 'var(--neg)',
      }} />
      {name}
    </span>
  )
}

export default function EnvHealthPanel() {
  const { data } = useApi('/api/env-health', 60000)
  if (!data) {
    return (
      <Card title={<><b>env health</b></>} meta="required + optional">
        <div className="empty-state"><div className="empty-state-title">loading env health</div></div>
      </Card>
    )
  }

  const required = data.required || {}
  const optional = data.optional || {}
  const missingRequired = Object.entries(required).filter(([, v]) => !v)

  return (
    <Card title={<><b>env health</b></>} meta={`${missingRequired.length} required missing`}>
      {missingRequired.length > 0 && (
        <div className="neg" style={{ fontSize: 11, marginBottom: 12, padding: '6px 0', borderLeft: '2px solid var(--neg)', paddingLeft: 10 }}>
          {missingRequired.length} required env var{missingRequired.length === 1 ? '' : 's'} missing:{' '}
          <span style={{ fontFamily: 'var(--mono)' }}>{missingRequired.map(([k]) => k).join(', ')}</span>
        </div>
      )}
      <div style={{ marginBottom: 14 }}>
        <div className="dim" style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 6 }}>required</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {sortMissingFirst(Object.entries(required)).map(([k, v]) => <VarPill key={k} name={k} set={v} />)}
        </div>
      </div>
      <div>
        <div className="dim" style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 6 }}>optional</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {sortMissingFirst(Object.entries(optional)).map(([k, v]) => <VarPill key={k} name={k} set={v} />)}
        </div>
      </div>
    </Card>
  )
}
