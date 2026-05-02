// / decision provenance

import { useMemo, useState } from 'react'
import Card from './ui/Card.jsx'
import DataTable from './ui/DataTable.jsx'
import Pill from './ui/Pill.jsx'
import SectionH from './ui/SectionH.jsx'
import { useApiLive } from '../hooks/useApiLive'
import { useApi } from '../hooks/useApi'
import DecisionChain from './decisions/DecisionChain.jsx'
import GateGrid from './decisions/GateGrid.jsx'
import SizingRationale from './decisions/SizingRationale.jsx'

const COLS = [
  { key: 'decision_id', label: 'decision_id', cellClass: () => 'dim tiny',
    render: (r) => (r.decision_id || '').slice(0, 18) },
  { key: 'created_at', label: 'ts', cellClass: () => 'dim tiny',
    render: (r) => formatTs(r.created_at) },
  { key: 'symbol', label: 'sym', cellClass: () => 'sym' },
  { key: 'signal_type', label: 'side',
    render: (r) => <span className={r.signal_type === 'sell' ? 'neg' : 'pos'}>{(r.signal_type || '').toUpperCase()}</span> },
  { key: 'strength', label: 'strength', align: 'right',
    render: (r) => r.strength != null ? Number(r.strength).toFixed(2) : '—' },
  { key: 'status', label: 'outcome',
    render: (r) => statusPill(r.status) },
]

function statusPill(s) {
  if (s === 'rejected') return <Pill variant="killed">rejected</Pill>
  if (s === 'processed') return <Pill variant="live">filled</Pill>
  return <Pill variant="paper">{s || 'pending'}</Pill>
}

function formatTs(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleTimeString([], { hour12: false })
  } catch {
    return iso
  }
}

export default function DecisionsTab({ initialDecisionId }) {
  const list = useApiLive('/api/decisions?limit=50', 30000, ['decision_made', 'trade_executed', 'gate_breach'])
  const rows = useMemo(() => list.data?.decisions || [], [list.data])
  const [override, setOverride] = useState(null)
  const selectedId = override ?? initialDecisionId ?? rows[0]?.decision_id ?? null

  return (
    <SectionH num="05" title="decisions" em="fill provenance"
      meta={<><span className="pill new">live</span> &nbsp; <code>/api/decisions/:id</code></>}>
      <p className="p" style={{ marginBottom: 14 }}>
        Every signal carries a <code>decision_id</code> ULID threaded through risk → sizing → fill.
      </p>

      {selectedId && <DecisionDrawer decisionId={selectedId} />}

      <div style={{ marginTop: 18 }}>
        <Card title={<><b>recent decisions</b> · last 50</>} meta="click to load chain" p0>
          <DataTable
            columns={COLS}
            rows={rows.map((r) => ({ ...r, id: r.decision_id }))}
            keyField="id"
            selectedKey={selectedId}
            onRowClick={(row) => setOverride(row.decision_id)}
            emptyMessage="no decisions yet"
          />
        </Card>
      </div>
    </SectionH>
  )
}

function DecisionDrawer({ decisionId }) {
  const { data, loading, error } = useApi(`/api/decisions/${decisionId}`, null)
  if (loading) return <div className="empty-state"><div className="empty-state-title">loading chain…</div></div>
  if (error) return <div className="empty-state"><div className="empty-state-title">no chain available — pre-migration</div></div>
  if (!data) return null

  const sig = data.signal || {}
  const app = data.approved
  const fill = data.fill
  const sizing = app?.sizing_details

  const steps = [
    { n: '01 · SIGNAL', nm: `${sig.strategy_id || '?'} · ${sig.signal_type || ''}`,
      d: sig.regime || '', out: `strength · ${sig.strength != null ? Number(sig.strength).toFixed(2) : '—'}`,
      outTone: 'pos' },
    { n: '02 · CONSENSUS', nm: 'analyst snapshot',
      d: data.analysis?.regime || 'no analysis row',
      out: data.analysis?.composite_score != null ? `composite · ${Number(data.analysis.composite_score).toFixed(2)}` : '—',
      outTone: 'pos' },
    { n: '03 · GATES', nm: '8-gate pre-trade check',
      d: sig.rejection_reason ? `blocked · ${sig.rejection_reason}` : 'all 8 passed',
      out: sig.status === 'rejected' ? 'breach' : 'pass',
      outTone: sig.status === 'rejected' ? 'neg' : 'pos' },
    { n: '04 · SIZE', nm: sizing?.regime ? `regime ${sizing.regime}` : 'sizing',
      d: sizing ? `kelly ${Number(sizing.kelly_fraction || 0).toFixed(3)} · ×${Number(sizing.regime_multiplier || 1).toFixed(2)}` : '—',
      out: app?.qty != null ? `qty · ${app.qty}` : '—' },
    { n: '05 · FILL', nm: fill?.broker || (app ? 'pending' : 'not approved'),
      d: fill ? `${fill.order_id || ''}` : '',
      out: fill?.price != null ? `filled · $${Number(fill.price).toFixed(2)}` : (app ? 'pending' : '—'),
      outTone: fill ? 'pos' : '' },
  ]

  return (
    <Card
      title={<><b>{sig.signal_type === 'sell' ? 'SELL' : 'BUY'}</b> · {sig.symbol || '—'} · {formatTs(sig.created_at)}</>}
      meta={<><code>{decisionId.slice(0, 18)}</code> · {statusPill(sig.status)}</>}
    >
      <DecisionChain steps={steps} />

      <div className="grid c12-8-4" style={{ marginTop: 18 }}>
        <div>
          <div style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 8 }}>8-gate trace</div>
          <GateGrid gates={data.gates || []} />
        </div>
        <div>
          <div style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 8 }}>why this size?</div>
          {sizing ? <SizingRationale details={sizing} /> : (
            <div className="empty-state" style={{ padding: '14px 0' }}>
              <div className="empty-state-hint">no sizing details — pre-migration</div>
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}
