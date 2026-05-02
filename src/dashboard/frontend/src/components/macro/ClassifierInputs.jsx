import Card from '../ui/Card'
import KVList from '../ui/KVList'
import { useApi } from '../../hooks/useApi'

// / regime classifier inputs

function regimeTone(value, kind) {
  if (value == null || !Number.isFinite(Number(value))) return 'dim'
  const v = Number(value)
  if (kind === 'spread') return v < 0 ? 'neg' : v < 0.3 ? 'warn' : 'pos'
  if (kind === 'vix')    return v > 25 ? 'neg' : v > 18 ? 'warn' : 'pos'
  return 'dim'
}

export default function ClassifierInputs() {
  const { data } = useApi('/api/macro-context', 60000)
  const indicators = data?.indicators || []
  const spread = data?.yield_curve_spread

  const bySeries = {}
  for (const r of indicators) bySeries[r.series_id] = r

  const dgs10 = bySeries.DGS10?.value
  const fedFunds = bySeries.FEDFUNDS?.value
  const cpi = bySeries.CPIAUCSL?.value
  const unrate = bySeries.UNRATE?.value
  const vix = bySeries.VIXCLS?.value

  const spreadTone = regimeTone(spread?.value, 'spread')
  const vixTone = regimeTone(vix, 'vix')

  const entries = [
    { k: '10Y yield', v: dgs10 != null ? `${Number(dgs10).toFixed(2)}%` : <span className="dim">—</span> },
    { k: 'Fed funds', v: fedFunds != null ? `${Number(fedFunds).toFixed(2)}%` : <span className="dim">—</span> },
    { k: '10Y-2Y spread', v: spread ? <>{spread.value >= 0 ? '+' : ''}{spread.value.toFixed(2)} · <span className={spreadTone}>{spread.inverted ? 'inverted' : spread.value < 0.3 ? 'flat' : 'normal'}</span></> : <span className="dim">—</span> },
    { k: 'CPI (raw)', v: cpi != null ? Number(cpi).toFixed(2) : <span className="dim">—</span> },
    { k: 'Unemployment', v: unrate != null ? `${Number(unrate).toFixed(2)}%` : <span className="dim">—</span> },
    { k: 'VIX', v: vix != null ? <>{Number(vix).toFixed(2)} · <span className={vixTone}>{Number(vix) > 25 ? 'high' : Number(vix) > 18 ? 'elevated' : 'low'}</span></> : <span className="dim">—</span> },
  ]

  return (
    <Card title={<><b>classifier inputs</b></>} meta={<><code>/api/macro-context</code></>}>
      {indicators.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">no classifier inputs yet</div>
          <div className="empty-state-hint">FRED backfill runs daily 9am ET.</div>
        </div>
      ) : (
        <KVList entries={entries} />
      )}
    </Card>
  )
}
