import Card from '../ui/Card'
import { useApi } from '../../hooks/useApi'

// / 5pm reasoner output

function asList(value) {
  if (Array.isArray(value)) return value
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return []
    }
  }
  return []
}

function pickLabel(item) {
  if (typeof item === 'string') return item
  return item?.symbol ?? item?.ticker ?? item?.name ?? '—'
}

function pickHint(item) {
  if (typeof item === 'string') return ''
  return item?.note ?? item?.reason ?? item?.thesis ?? ''
}

function ItemList({ items, tone }) {
  if (!items || items.length === 0) {
    return (
      <div className="empty-state-hint" style={{ fontSize: 11.5, padding: '8px 0', color: 'var(--ink-3)', fontFamily: 'var(--mono)' }}>
        no candidates yet
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {items.slice(0, 6).map((item, i) => (
        <div key={i} style={{ fontSize: 12, fontFamily: 'var(--mono)' }}>
          <span style={{ color: tone === 'pos' ? 'var(--pos)' : 'var(--neg)', fontWeight: 500 }}>
            {pickLabel(item)}
          </span>
          {pickHint(item) && (
            <span className="dim" style={{ marginLeft: 8, fontFamily: 'var(--sans)', fontSize: 11.5 }}>
              {pickHint(item)}
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

export default function SynthesisCard() {
  const { data, loading } = useApi('/api/synthesis', 300000)

  const buys = asList(data?.top_buys)
  const avoids = asList(data?.top_avoids)
  const note = data?.portfolio_risk
  const date = data?.date

  return (
    <Card
      title={<><b>daily synthesis</b> · 5pm reasoner</>}
      meta={<><code>/api/synthesis</code>{date ? ` · ${date}` : ''}</>}
    >
      {loading && !data ? (
        <div className="empty-state"><div className="empty-state-title">loading synthesis</div></div>
      ) : !data ? (
        <div className="empty-state">
          <div className="empty-state-title">no synthesis yet</div>
          <div className="empty-state-hint">first daily synthesis is written at the 5pm reasoner cycle.</div>
        </div>
      ) : (
        <>
          <div className="grid c2" style={{ gap: 14 }}>
            <div>
              <div style={{ fontSize: 11, color: 'var(--pos)', letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 8 }}>
                top buy candidates
              </div>
              <ItemList items={buys} tone="pos" />
            </div>
            <div>
              <div style={{ fontSize: 11, color: 'var(--neg)', letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 8 }}>
                top avoid candidates
              </div>
              <ItemList items={avoids} tone="neg" />
            </div>
          </div>
          {note && (
            <p className="p" style={{ fontSize: 11.5, marginTop: 14, color: 'var(--ink-3)', fontFamily: 'var(--mono)' }}>
              portfolio note: <em>{note}</em>
            </p>
          )}
        </>
      )}
    </Card>
  )
}
