// / risk dashboard

import Card from './ui/Card.jsx'
import DataTable from './ui/DataTable.jsx'
import Gauge, { GaugeRow } from './ui/Gauge.jsx'
import Pill from './ui/Pill.jsx'
import SectionH from './ui/SectionH.jsx'
import { useApiLive } from '../hooks/useApiLive'
import CorrelationCluster from './risk/CorrelationCluster.jsx'

const GATE_COLS = [
  { key: 'name', label: 'gate' },
  { key: 'rule', label: 'rule', cellClass: () => 'dim' },
  { key: 'value', label: 'value', align: 'right',
    render: (r) => fmtVal(r.value) },
  { key: 'limit', label: 'limit', align: 'right', cellClass: () => 'r dim',
    render: (r) => fmtVal(r.limit) },
  { key: 'status', label: 'status', align: 'right',
    render: (r) => statusPill(r.status) },
]

function fmtVal(v) {
  if (v == null) return '—'
  if (typeof v !== 'number') return String(v)
  if (Math.abs(v) < 1) return v.toFixed(3)
  return v.toLocaleString()
}

function statusPill(s) {
  if (s === 'pass') return <Pill variant="live">pass</Pill>
  if (s === 'warn') return <Pill variant="paper">tight</Pill>
  if (s === 'fail') return <Pill variant="killed">fail</Pill>
  return <Pill variant="new">—</Pill>
}

export default function RiskTab() {
  const { data } = useApiLive('/api/risk/gauges', 30000, ['position_update', 'trade_executed'])
  const corr = useApiLive('/api/portfolio/correlation', 60000, ['position_update'])
  const gauges = data?.gauges || {}
  const gates = data?.gates || []

  return (
    <SectionH num="06" title="risk & gates"
      meta={<><code>configs/risk_limits.json</code> · {gates.length} gates</>}>
      <Card>
        <GaugeRow>
          <Gauge label={gauges.var_95?.label || 'VaR (95%)'}
            value={gauges.var_95?.value != null ? `$${Number(gauges.var_95.value).toLocaleString()}` : '—'}
            limit={gauges.var_95?.limit != null ? `limit $${Number(gauges.var_95.limit).toLocaleString()}` : ''}
            fillRatio={ratio(gauges.var_95?.value, gauges.var_95?.limit)} />
          <Gauge label="tail dep λ"
            value={gauges.tail_dep_lambda?.value != null ? Number(gauges.tail_dep_lambda.value).toFixed(2) : '—'}
            limit={`trigger ${Number(gauges.tail_dep_lambda?.limit || 0).toFixed(2)}`}
            fillRatio={ratio(gauges.tail_dep_lambda?.value, gauges.tail_dep_lambda?.limit)} />
          <Gauge label="drawdown"
            value={gauges.drawdown_pct?.value != null ? `${(gauges.drawdown_pct.value * 100).toFixed(1)}%` : '—'}
            valueTone={gauges.drawdown_pct?.value < -0.05 ? 'warn' : ''}
            limit={`stop ${Number(gauges.drawdown_pct?.limit || 0) * 100}%`}
            fillRatio={ddRatio(gauges.drawdown_pct?.value, gauges.drawdown_pct?.limit)}
            fillTone="warn" />
          <Gauge label="gross exposure"
            value={gauges.gross_exposure_pct?.value != null ? `${(gauges.gross_exposure_pct.value * 100).toFixed(0)}%` : '—'}
            limit={`cap ${Number(gauges.gross_exposure_pct?.limit || 0) * 100}%`}
            fillRatio={ratio(gauges.gross_exposure_pct?.value, gauges.gross_exposure_pct?.limit)} />
        </GaugeRow>
      </Card>

      <div className="grid c12-8-4" style={{ marginTop: 18 }}>
        <Card title={<><b>gates</b> · pre-trade checks</>} meta="applied per signal" p0>
          <DataTable columns={GATE_COLS} rows={gates.map((g, i) => ({ ...g, id: i }))} keyField="id" />
        </Card>
        <Card title={<><b>correlation</b> · clusters</>} meta="force layout">
          <CorrelationCluster symbols={corr.data?.symbols} matrix={corr.data?.matrix} />
          <p style={{ fontSize: 10.5, color: 'var(--ink-3)', margin: '10px 0 0', fontFamily: 'var(--mono)', lineHeight: 1.65 }}>
            edges drawn for |ρ| ≥ 0.4 · cluster sum capped per gate.
          </p>
        </Card>
      </div>
    </SectionH>
  )
}

function ratio(v, lim) {
  if (v == null || lim == null || lim === 0) return 0
  return Math.max(0, Math.min(1, Math.abs(v) / Math.abs(lim)))
}

function ddRatio(v, lim) {
  if (v == null || lim == null || lim === 0) return 0
  return Math.max(0, Math.min(1, Math.abs(v) / Math.abs(lim)))
}
