import Sparkline from '../ui/Sparkline'

// / macro tape tile

const SERIES_META = {
  DGS10:    { label: '10Y yield',     unit: '%',  higherIs: 'neutral', tone: 'acc2',
              low: 2.0,  high: 4.5,  lowTag: 'low',     highTag: 'high',  midTag: 'normal',
              signals: { low: 'bullish', mid: 'neutral', high: 'bearish' } },
  DGS2:     { label: '2Y yield',      unit: '%',  higherIs: 'neutral', tone: 'acc2',
              low: 2.0,  high: 4.5,  lowTag: 'low',     highTag: 'high',  midTag: 'normal',
              signals: { low: 'bullish', mid: 'neutral', high: 'bearish' } },
  CPIAUCSL: { label: 'CPI',           unit: '',   higherIs: 'bearish', tone: 'warn',
              low: 2.0,  high: 3.0,  lowTag: 'target',  highTag: 'hot',   midTag: 'above target',
              signals: { low: 'bullish', mid: 'neutral', high: 'bearish' } },
  FEDFUNDS: { label: 'Fed funds',     unit: '%',  higherIs: 'bearish', tone: 'acc2',
              low: 2.0,  high: 4.5,  lowTag: 'easy',    highTag: 'tight', midTag: 'neutral',
              signals: { low: 'bullish', mid: 'neutral', high: 'bearish' } },
  UNRATE:   { label: 'Unemployment',  unit: '%',  higherIs: 'bearish', tone: 'warn',
              low: 4.0,  high: 5.0,  lowTag: 'tight',   highTag: 'slack', midTag: 'neutral',
              signals: { low: 'neutral', mid: 'bullish', high: 'bearish' } },
  VIXCLS:   { label: 'VIX',           unit: '',   higherIs: 'bearish', tone: 'pos',
              low: 15.0, high: 25.0, lowTag: 'calm',    highTag: 'fear',  midTag: 'normal',
              signals: { low: 'bullish', mid: 'neutral', high: 'bearish' } },
  DTWEXBGS: { label: 'DXY',           unit: '',   higherIs: 'neutral', tone: 'acc2',
              low: 95.0, high: 105.0, lowTag: 'weak',   highTag: 'strong', midTag: 'normal',
              signals: { low: 'bullish', mid: 'neutral', high: 'bearish' } },
  DCOILWTICO: { label: 'WTI',         unit: '$',  higherIs: 'neutral', tone: 'warn',
              low: 50.0, high: 90.0, lowTag: 'low',     highTag: 'high',  midTag: 'normal',
              signals: { low: 'neutral', mid: 'neutral', high: 'bearish' } },
}

const SIGNAL_TONE = { bullish: 'pos', bearish: 'neg', neutral: 'dim' }

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

function zoneFor(value, meta) {
  if (value == null || !Number.isFinite(value) || meta.low == null) return null
  if (value < meta.low) return { tag: meta.lowTag, pos: 'low' }
  if (value > meta.high) return { tag: meta.highTag, pos: 'high' }
  return { tag: meta.midTag, pos: 'mid' }
}

function zoneTone(zone, higherIs) {
  if (!zone) return 'dim'
  if (higherIs === 'bearish') {
    return zone.pos === 'high' ? 'neg' : zone.pos === 'low' ? 'pos' : 'warn'
  }
  if (higherIs === 'bullish') {
    return zone.pos === 'high' ? 'pos' : zone.pos === 'low' ? 'neg' : 'warn'
  }
  return zone.pos === 'mid' ? 'pos' : 'warn'
}

function fmtThresh(v, unit) {
  if (v == null) return ''
  return `${v}${unit || ''}`
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

  const zone = zoneFor(value, meta)
  const zTone = zoneTone(zone, meta.higherIs)
  const signal = (zone && meta.signals) ? meta.signals[zone.pos] : null
  const signalTone = signal ? SIGNAL_TONE[signal] : 'dim'

  let markerPct = null
  if (value != null && meta.low != null && meta.high != null) {
    const span = meta.high - meta.low
    const min = meta.low - span
    const max = meta.high + span
    const clamped = Math.max(min, Math.min(max, value))
    markerPct = ((clamped - min) / (max - min)) * 100
  }
  const lowPct = 33.33
  const highPct = 66.67

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
        {zone && (
          <div style={{ marginTop: 2, display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap' }}>
            <span className={zTone} style={{ fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
              {zone.tag}
            </span>
            {signal && (
              <span className={signalTone} style={{ fontFamily: 'var(--mono)', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                · {signal} for stocks
              </span>
            )}
          </div>
        )}
        <Sparkline data={points} width={200} height={30} tone={meta.tone} />
        {meta.low != null && (
          <div style={{ marginTop: 4 }}>
            <div style={{ position: 'relative', height: 4, background: 'var(--bg)', border: '1px solid var(--line)' }}>
              <div style={{ position: 'absolute', top: 0, bottom: 0, left: 0, width: `${lowPct}%`, background: 'rgba(127,184,122,0.18)' }} />
              <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${lowPct}%`, width: `${highPct - lowPct}%`, background: 'rgba(199,154,58,0.18)' }} />
              <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${highPct}%`, right: 0, background: 'rgba(213,106,91,0.18)' }} />
              {markerPct != null && (
                <div style={{ position: 'absolute', top: -2, bottom: -2, left: `calc(${markerPct}% - 1px)`, width: 2, background: 'var(--ink)' }} />
              )}
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--ink-4)', marginTop: 2 }}>
              <span>{fmtThresh(meta.low, meta.unit)}</span>
              <span>{fmtThresh(meta.high, meta.unit)}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
