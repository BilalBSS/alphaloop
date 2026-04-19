import { useMemo } from 'react'
import Panel from './Panel'
import HeroBanner from './HeroBanner'
import EmptyState from './EmptyState'
import { useApi } from '../hooks/useApi'

// / fred series metadata: how to label, what unit, how to colour movement
const SERIES_META = {
  DGS10:    { label: '10Y Treasury',   unit: '%', higherIs: 'neutral' },
  DGS2:     { label: '2Y Treasury',    unit: '%', higherIs: 'neutral' },
  CPIAUCSL: { label: 'CPI (YoY)',      unit: '',  higherIs: 'bearish' },
  FEDFUNDS: { label: 'Fed funds rate', unit: '%', higherIs: 'bearish' },
  UNRATE:   { label: 'Unemployment',   unit: '%', higherIs: 'bearish' },
}

function timeAgo(ts) {
  if (!ts) return 'never'
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 0) return 'just now'
  const h = Math.floor(diff / 3600000)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

// / inline svg sparkline — no chart library needed, <50 lines of d3-free math
function Sparkline({ points, width = 120, height = 36, color = 'currentColor' }) {
  if (!points || points.length < 2) {
    return (
      <svg width={width} height={height} className="text-text-muted">
        <line x1="0" y1={height / 2} x2={width} y2={height / 2} stroke="currentColor" strokeDasharray="3 3" opacity="0.4" />
      </svg>
    )
  }
  const values = points.map((p) => p.value).filter((v) => v != null)
  if (values.length < 2) return null
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const step = width / (points.length - 1)
  const d = points.map((p, i) => {
    const x = i * step
    const y = height - ((p.value - min) / range) * (height - 4) - 2
    return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
  }).join(' ')
  return (
    <svg width={width} height={height} className="overflow-visible">
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function yieldCurveLabel(spread) {
  if (!spread) return { label: 'unknown', tone: 'muted' }
  if (spread.inverted) return { label: 'inverted', tone: 'negative' }
  if (spread.value < 0.3) return { label: 'flat', tone: 'warning' }
  return { label: 'normal', tone: 'positive' }
}

function regimeLabel(indicators, spread) {
  // / derive a coarse macro regime from latest values + yield curve
  const ff = indicators.find((r) => r.series_id === 'FEDFUNDS')
  if (!ff) return { label: 'pending', tone: 'muted' }
  if (spread?.inverted) return { label: 'recession risk', tone: 'negative' }
  if (parseFloat(ff.value) > 4.5) return { label: 'tightening', tone: 'warning' }
  if (parseFloat(ff.value) < 2.0) return { label: 'easing', tone: 'positive' }
  return { label: 'neutral', tone: 'muted' }
}

function MetricTile({ seriesId, latest, history }) {
  const meta = SERIES_META[seriesId] || { label: seriesId, unit: '', higherIs: 'neutral' }
  const points = history[seriesId] || []
  const value = latest ? parseFloat(latest.value) : null
  const first = points[0]?.value
  const last = points[points.length - 1]?.value
  const delta = (first != null && last != null) ? last - first : null
  const deltaPct = (first != null && last != null && first !== 0) ? (delta / Math.abs(first)) * 100 : null

  const deltaTone =
    delta == null ? 'muted'
    : (meta.higherIs === 'bearish' ? (delta > 0 ? 'negative' : 'positive')
       : meta.higherIs === 'bullish' ? (delta > 0 ? 'positive' : 'negative')
       : delta > 0 ? 'positive' : 'negative')

  const deltaClass = {
    positive: 'pnl-positive',
    negative: 'pnl-negative',
    muted: 'text-text-muted',
  }[deltaTone]

  return (
    <div className="bg-bg-primary border border-border rounded p-3 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-text-muted">{meta.label}</div>
          <div className="text-[9px] font-mono text-text-muted">{seriesId}</div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-mono text-text-primary">
            {value != null ? value.toFixed(2) : '—'}
            {meta.unit && <span className="text-xs text-text-secondary ml-0.5">{meta.unit}</span>}
          </div>
          {delta != null && (
            <div className={`text-[10px] font-mono ${deltaClass}`}>
              {delta > 0 ? '+' : ''}{delta.toFixed(2)}{meta.unit} · {deltaPct > 0 ? '+' : ''}{deltaPct?.toFixed(1)}%
            </div>
          )}
        </div>
      </div>
      <div className={deltaClass}>
        <Sparkline points={points} />
      </div>
      <div className="text-[10px] text-text-muted">
        {latest?.date ? `updated ${timeAgo(latest.date)}` : 'no data'}
      </div>
    </div>
  )
}

export default function MacroTab() {
  const { data: context, loading: loadingCtx } = useApi('/api/macro-context', 60000)
  const { data: history } = useApi('/api/macro-history?days=180', 60000)

  const indicators = context?.indicators || []
  const spread = context?.yield_curve_spread
  const latestBySeries = useMemo(() => {
    const map = {}
    for (const r of indicators) map[r.series_id] = r
    return map
  }, [indicators])

  const curve = yieldCurveLabel(spread)
  const regime = regimeLabel(indicators, spread)

  if (loadingCtx && !indicators.length) {
    return <EmptyState title="Loading FRED indicators…" />
  }
  if (!indicators.length) {
    return (
      <div className="space-y-4">
        <HeroBanner>
          <div className="hero-metric">
            <span className="hero-metric-label">Macro regime</span>
            <span className="hero-metric-value text-text-muted">pending</span>
          </div>
        </HeroBanner>
        <EmptyState
          title="No macro data yet"
          hint='FRED backfill runs daily at 9am ET. Use the System tab "Run now" on macro_backfill to kick it immediately, or set FRED_API_KEY in the VPS env.'
        />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <HeroBanner>
        <div className="hero-metric">
          <span className="hero-metric-label">Macro regime</span>
          <span className={`hero-metric-value ${
            regime.tone === 'negative' ? 'pnl-negative'
            : regime.tone === 'positive' ? 'pnl-positive'
            : regime.tone === 'warning' ? 'text-warning' : 'text-text-muted'
          }`}>
            {regime.label}
          </span>
        </div>
        <div className="hero-metric">
          <span className="hero-metric-label">10Y-2Y spread</span>
          <span className={`hero-metric-value font-mono ${
            curve.tone === 'negative' ? 'pnl-negative'
            : curve.tone === 'positive' ? 'pnl-positive'
            : curve.tone === 'warning' ? 'text-warning' : 'text-text-muted'
          }`}>
            {spread ? `${spread.value > 0 ? '+' : ''}${spread.value.toFixed(2)}` : '—'}
          </span>
        </div>
        <div className="hero-metric">
          <span className="hero-metric-label">Yield curve</span>
          <span className="hero-metric-value-sm text-text-secondary">{curve.label}</span>
        </div>
        <div className="hero-metric">
          <span className="hero-metric-label">Series populated</span>
          <span className="hero-metric-value font-mono">{indicators.length}/5</span>
        </div>
      </HeroBanner>

      <Panel title="FRED indicators">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {Object.keys(SERIES_META).map((sid) => (
            <MetricTile
              key={sid}
              seriesId={sid}
              latest={latestBySeries[sid]}
              history={history?.series || {}}
            />
          ))}
        </div>
      </Panel>

      <Panel title="Notes">
        <div className="text-xs text-text-secondary space-y-1.5">
          <div>• Yield curve inversion (10Y-2Y &lt; 0) has historically preceded recessions by 6–18 months.</div>
          <div>• Series refresh daily at 9am ET via the macro_backfill loop — check the System tab for last-fire timestamp.</div>
          <div>• Sparklines show the last 30 days. Values are raw FRED observations; normalized versions feed the strategy layer.</div>
        </div>
      </Panel>
    </div>
  )
}
