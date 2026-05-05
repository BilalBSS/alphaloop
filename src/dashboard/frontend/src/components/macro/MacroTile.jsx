import Sparkline from '../ui/Sparkline'

// / macro tape tile

const SERIES_META = {
  DGS10:    { label: '10Y yield',     unit: '%',  higherIs: 'neutral', tone: 'acc2' },
  DGS2:     { label: '2Y yield',      unit: '%',  higherIs: 'neutral', tone: 'acc2' },
  CPIAUCSL: { label: 'CPI',           unit: '',   higherIs: 'bearish', tone: 'warn' },
  FEDFUNDS: { label: 'Fed funds',     unit: '%',  higherIs: 'bearish', tone: 'acc2' },
  UNRATE:   { label: 'Unemployment',  unit: '%',  higherIs: 'bearish', tone: 'warn' },
  VIXCLS:     { label: 'VIX', unit: '',  higherIs: 'bearish', tone: 'pos' },
  DTWEXBGS:   { label: 'DXY', unit: '',  higherIs: 'neutral', tone: 'acc2' },
  DCOILWTICO: { label: 'WTI', unit: '$', higherIs: 'neutral', tone: 'warn' },
}

function formatValue(v, unit) {
  if (v == null) return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  const abs = Math.abs(n)
  const fixed = abs >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 0 })
              : abs >= 100 ? n.toFixed(1)
              : n.toFixed(2)
  return `${fixed}${unit || ''}`
}

export default function MacroTile({ seriesId, latest, history }) {
  const meta = SERIES_META[seriesId] || { label: seriesId, unit: '', higherIs: 'neutral', tone: 'ink' }
  const points = (history?.[seriesId] || []).map((p) => Number(p.value)).filter((v) => Number.isFinite(v))
  const value = latest ? Number(latest.value) : null
  const first = points[0]
  const last = points[points.length - 1]
  const delta = (first != null && last != null) ? last - first : null

  const tone = delta == null ? 'dim'
    : meta.higherIs === 'bearish' ? (delta > 0 ? 'neg' : 'pos')
    : meta.higherIs === 'bullish' ? (delta > 0 ? 'pos' : 'neg')
    : delta > 0 ? 'pos' : 'neg'

  return (
    <div className="card">
      <div className="card-b">
        <div style={{ fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--ink-3)' }}>
          {meta.label}
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginTop: 8 }}>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 22, fontWeight: 600 }}>
            {formatValue(value, meta.unit)}
          </div>
          {delta != null && (
            <div className={tone} style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>
              {delta >= 0 ? '+' : ''}{delta.toFixed(2)}
            </div>
          )}
        </div>
        <Sparkline data={points} width={200} height={30} tone={meta.tone} />
      </div>
    </div>
  )
}
