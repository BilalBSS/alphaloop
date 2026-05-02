import { useApi } from '../../hooks/useApi'
import TimeSeriesLineChart from '../chart/TimeSeriesLineChart'

// / directional arrow vs prior
function DirArrow({ current, prior }) {
  if (current == null || prior == null) return null
  const c = parseFloat(current)
  const p = parseFloat(prior)
  if (!Number.isFinite(c) || !Number.isFinite(p)) return null
  if (c > p) return <span className="pos" style={{ marginLeft: 4 }}>▲</span>
  if (c < p) return <span className="neg" style={{ marginLeft: 4 }}>▼</span>
  return <span className="dim" style={{ marginLeft: 4 }}>━</span>
}

// / macro regime tiles
export function MacroRegimeCard() {
  const { data, loading, error } = useApi('/api/macro-context', 300000)

  if (loading && !data) return <div className="skeleton" style={{ height: 96 }} />
  if (error) return <div className="neg" style={{ padding: 14, fontSize: 12 }}>Failed to load macro: {error}</div>
  if (!data) return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No macro data</div>

  const prior = data.prior || {}
  const rows = [
    { key: 'dff', label: 'fed funds', fmt: v => `${parseFloat(v).toFixed(2)}%` },
    { key: 'cpi', label: 'cpi YoY', fmt: v => `${parseFloat(v).toFixed(2)}%` },
    { key: 'unrate', label: 'unemployment', fmt: v => `${parseFloat(v).toFixed(1)}%` },
    { key: 'yield_spread', label: '10Y-2Y spread', fmt: v => `${parseFloat(v).toFixed(2)}%` },
  ]

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div className="grid c2" style={{ gap: 8 }}>
        {rows.map(r => {
          const v = data[r.key]
          const p = prior[r.key]
          if (v == null) {
            return (
              <div className="tile" key={r.key}>
                <div className="lab">{r.label}</div>
                <div className="v dim">—</div>
              </div>
            )
          }
          return (
            <div className="tile" key={r.key}>
              <div className="lab">{r.label}</div>
              <div className="v">
                {r.fmt(v)}
                <DirArrow current={v} prior={p} />
              </div>
            </div>
          )
        })}
      </div>
      {data.updated_at && (
        <div className="dim" style={{ fontSize: 10, fontFamily: 'var(--mono)' }}>
          updated {data.updated_at.replace('T', ' ').slice(0, 16)}
        </div>
      )}
    </div>
  )
}

// / analyst consensus card
export function AnalystConsensusCard({ symbol }) {
  const { data, loading, error } = useApi(`/api/analyst-ratings/${symbol}`, 300000)

  if (loading && !data) return <div className="skeleton" style={{ height: 96 }} />
  if (error) return <div className="neg" style={{ padding: 14, fontSize: 12 }}>Failed to load: {error}</div>
  const latest = data?.consensus_score != null ? data : (Array.isArray(data?.history) ? data.history[0] : null)
  if (!latest || latest.consensus_score == null) {
    return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No analyst ratings</div>
  }

  const score = parseFloat(latest.consensus_score) || 0
  const pct = ((score + 1) / 2) * 100
  const tone = score >= 0.4 ? 'pos' : score <= -0.4 ? 'neg' : 'warn'
  const label = score >= 0.6 ? 'strong buy' : score >= 0.2 ? 'buy' : score > -0.2 ? 'hold' : score > -0.6 ? 'sell' : 'strong sell'

  const dist = [
    { key: 'strong_buy', label: 'SB', count: latest.strong_buy || 0, color: 'var(--pos)' },
    { key: 'buy', label: 'B', count: latest.buy || 0, color: 'rgba(127,184,122,.6)' },
    { key: 'hold', label: 'H', count: latest.hold || 0, color: 'var(--warn)' },
    { key: 'sell', label: 'S', count: latest.sell || 0, color: 'rgba(213,106,91,.6)' },
    { key: 'strong_sell', label: 'SS', count: latest.strong_sell || 0, color: 'var(--neg)' },
  ]
  const totalCount = dist.reduce((s, d) => s + d.count, 0)

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div>
        <div style={{ fontSize: 24, fontWeight: 600, color: 'var(--ink)', lineHeight: 1, textTransform: 'uppercase' }}>{label}</div>
        <div className="dim" style={{ fontSize: 11, marginTop: 4 }}>
          {totalCount > 0 ? `${totalCount} covering` : 'no count'}
          {' · '}
          <span className={tone}>{score.toFixed(2)}</span>
        </div>
      </div>
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--ink-3)', marginBottom: 4 }}>
          <span>strong sell</span><span>hold</span><span>strong buy</span>
        </div>
        <div style={{ position: 'relative', height: 6, background: 'var(--bg)', border: '1px solid var(--line)' }}>
          <div style={{ position: 'absolute', top: 0, bottom: 0, left: '50%', width: 1, background: 'var(--ink-4)' }} />
          <div style={{ position: 'absolute', top: 0, bottom: 0, left: `calc(${pct}% - 1.5px)`, width: 3, background: 'var(--acc)' }} />
        </div>
      </div>
      {totalCount > 0 && (
        <div>
          <div style={{ display: 'flex', height: 6, border: '1px solid var(--line)' }}>
            {dist.map(d => {
              const w = (d.count / totalCount) * 100
              if (w === 0) return null
              return <div key={d.key} style={{ width: `${w}%`, background: d.color }} title={`${d.label}: ${d.count}`} />
            })}
          </div>
          <div className="dim" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, marginTop: 4, fontFamily: 'var(--mono)' }}>
            {dist.map(d => <span key={d.key}>{d.label}:{d.count}</span>)}
          </div>
        </div>
      )}
      {latest.target_mean != null && (
        <div className="dim" style={{ fontSize: 10.5, fontFamily: 'var(--mono)' }}>
          target <b style={{ color: 'var(--ink)' }}>${parseFloat(latest.target_mean).toFixed(2)}</b>
        </div>
      )}
    </div>
  )
}

// / options flow card
export function OptionsFlowCard({ symbol }) {
  const { data, loading, error } = useApi(`/api/options/${symbol}`, 300000)

  if (loading && !data) return <div className="skeleton" style={{ height: 96 }} />
  if (error) return <div className="neg" style={{ padding: 14, fontSize: 12 }}>Failed to load: {error}</div>
  const latest = data?.latest ?? (Array.isArray(data?.history) ? data.history[0] : data)
  if (!latest || (latest.iv_rank == null && latest.put_call_ratio == null && latest.max_pain == null)) {
    return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No options data</div>
  }

  const ivRaw = latest.iv_rank != null ? parseFloat(latest.iv_rank) : null
  const ivRank = ivRaw == null ? null : (ivRaw <= 1 ? ivRaw * 100 : ivRaw)
  const pcr = latest.put_call_ratio != null ? parseFloat(latest.put_call_ratio) : null
  const maxPain = latest.max_pain != null ? parseFloat(latest.max_pain) : null

  const ivTone = ivRank == null ? 'dim' : ivRank > 70 ? 'neg' : ivRank > 40 ? 'warn' : 'pos'
  const pcrTone = pcr == null ? 'dim' : pcr > 1.2 ? 'neg' : pcr > 0.8 ? 'warn' : 'pos'

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div className="grid c3" style={{ gap: 8 }}>
        <div className="tile">
          <div className="lab">IV rank</div>
          <div className={`v ${ivTone}`}>{ivRank == null ? '—' : `${ivRank.toFixed(0)}%`}</div>
        </div>
        <div className="tile">
          <div className="lab">put/call</div>
          <div className={`v ${pcrTone}`}>{pcr == null ? '—' : pcr.toFixed(2)}</div>
          <div className="x">{pcr != null && (pcr > 1.2 ? 'bearish' : pcr > 0.8 ? 'neutral' : 'bullish')}</div>
        </div>
        <div className="tile">
          <div className="lab">max pain</div>
          <div className="v">{maxPain == null ? '—' : `$${maxPain.toFixed(0)}`}</div>
        </div>
      </div>
      {Array.isArray(data?.history) && data.history.length > 0 && (
        <div>
          <div style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 4 }}>IV rank trend</div>
          <TimeSeriesLineChart
            data={data.history}
            timeKey="date"
            valueKey="iv_rank"
            color="#c79a3a"
            height={70}
            valueFmt={v => `${v.toFixed(0)}%`}
            emptyText="—"
          />
        </div>
      )}
    </div>
  )
}

// / dark pool card
export function DarkPoolCard({ symbol }) {
  const { data, loading, error } = useApi(`/api/dark-pool/${symbol}`, 300000)

  if (loading && !data) return <div className="skeleton" style={{ height: 96 }} />
  if (error) return <div className="neg" style={{ padding: 14, fontSize: 12 }}>Failed to load: {error}</div>
  const latest = data?.latest ?? (Array.isArray(data?.history) ? data.history[0] : data)
  const rawRatio = latest?.dark_pool_ratio ?? latest?.latest_ratio
  if (!latest || rawRatio == null) {
    return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No dark pool data</div>
  }

  const pct = (parseFloat(rawRatio) || 0) * 100
  const tone = pct > 45 ? 'acc' : pct > 35 ? 'warn' : ''
  const label = pct > 45 ? 'heavy institutional' : pct > 35 ? 'elevated' : 'normal'

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div>
        <div style={{ fontSize: 24, fontWeight: 600, color: 'var(--ink)', lineHeight: 1 }}>
          {pct.toFixed(1)}%
        </div>
        <div className="dim" style={{ fontSize: 11, marginTop: 4 }}>
          ratio · <span className={tone}>{label}</span>
        </div>
      </div>
      {Array.isArray(data?.history) && data.history.length > 0 && (
        <div>
          <div style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 4 }}>weekly trend</div>
          <TimeSeriesLineChart
            data={[...data.history].reverse()}
            timeKey="week_start"
            valueKey="dark_pool_ratio"
            color="#d8b466"
            height={70}
            valueFmt={v => `${(v * 100).toFixed(1)}%`}
            emptyText="—"
          />
        </div>
      )}
    </div>
  )
}

// / congressional activity card
export function CongressionalCard({ symbol }) {
  const { data, loading, error } = useApi(`/api/congressional/${symbol}`, 300000)

  if (loading && !data) return <div className="skeleton" style={{ height: 96 }} />
  if (error) return <div className="neg" style={{ padding: 14, fontSize: 12 }}>Failed to load: {error}</div>
  if (!data || !data.trades || data.trades.length === 0) {
    return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No congressional trades</div>
  }

  const trades = data.trades || []
  const ratioRaw = data.net_buy_ratio ?? data.ratio
  const ratio = ratioRaw != null ? parseFloat(ratioRaw) : null
  const buys = trades.filter(t => (t.transaction_type || t.side || '').toLowerCase().includes('buy')).length
  const sells = trades.length - buys
  const total = buys + sells
  const buyPct = total > 0 ? (buys / total) * 100 : 0
  const sellPct = total > 0 ? (sells / total) * 100 : 0
  const ratioTone = ratio == null ? 'dim' : ratio > 0.6 ? 'pos' : ratio < 0.4 ? 'neg' : 'warn'

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div className="grid c2" style={{ gap: 8 }}>
        <div className="tile">
          <div className="lab">buy ratio</div>
          <div className={`v ${ratioTone}`}>{ratio == null ? '—' : ratio.toFixed(2)}</div>
          <div className="x">{buys} buys · {sells} sells</div>
        </div>
        <div className="tile">
          <div className="lab">split</div>
          <div style={{ display: 'flex', height: 8, marginTop: 6, border: '1px solid var(--line)' }}>
            {buys > 0 && <div style={{ width: `${buyPct}%`, background: 'var(--pos)' }} />}
            {sells > 0 && <div style={{ width: `${sellPct}%`, background: 'var(--neg)' }} />}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, fontFamily: 'var(--mono)', marginTop: 4 }}>
            <span className="pos">{buyPct.toFixed(0)}%</span>
            <span className="neg">{sellPct.toFixed(0)}%</span>
          </div>
        </div>
      </div>
      <div style={{ maxHeight: 200, overflowY: 'auto', borderTop: '1px solid var(--line)' }}>
        {trades.slice(0, 10).map((t, i) => {
          const type = (t.transaction_type || t.side || '—').toString()
          const isBuy = type.toLowerCase().includes('buy')
          const date = t.filing_date?.split('T')[0] || t.date?.split('T')[0] || '—'
          return (
            <div className="feed-row" key={i}>
              <span className="ts">{date}</span>
              <span className="nm" title={t.member || t.name}><b>{t.member || t.name || '—'}</b></span>
              <span className={`v ${isBuy ? 'pos' : 'neg'}`}>{type.toUpperCase()}</span>
              <span className="v dim">{t.amount_range || t.amount || '—'}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// / short squeeze card
export function ShortSqueezeCard({ symbol }) {
  const { data, loading, error } = useApi(`/api/short/${symbol}`, 300000)

  if (loading && !data) return <div className="skeleton" style={{ height: 96 }} />
  if (error) return <div className="neg" style={{ padding: 14, fontSize: 12 }}>Failed to load: {error}</div>
  const latest = data?.latest ?? (Array.isArray(data?.history) ? data.history[0] : data)
  const rawRatio = latest?.short_ratio ?? latest?.short_pct_float
  if (!latest || rawRatio == null) {
    return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No short interest data</div>
  }

  const ratio = parseFloat(rawRatio) || 0
  const tone = ratio > 10 ? 'neg' : ratio > 5 ? 'warn' : 'dim'
  const label = ratio > 10 ? 'squeeze risk' : ratio > 5 ? 'elevated' : 'normal'

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div className="tile">
        <div className="lab">days to cover</div>
        <div className={`v ${tone}`}>{ratio.toFixed(2)}</div>
        <div className="x">{label}{latest.short_volume != null && ` · short vol ${Number(latest.short_volume).toLocaleString()}`}</div>
      </div>
      {Array.isArray(data?.history) && data.history.length > 0 && (
        <div>
          <div style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 4 }}>trend</div>
          <TimeSeriesLineChart
            data={[...data.history].reverse()}
            timeKey="date"
            valueKey="short_ratio"
            color="#c79a3a"
            height={70}
            valueFmt={v => `${v.toFixed(2)}`}
            emptyText="—"
          />
        </div>
      )}
    </div>
  )
}

// / earnings revisions card
export function EarningsRevisionsCard({ symbol }) {
  const { data, loading, error } = useApi(`/api/earnings-revisions/${symbol}`, 300000)

  if (loading && !data) return <div className="skeleton" style={{ height: 96 }} />
  if (error) return <div className="neg" style={{ padding: 14, fontSize: 12 }}>Failed to load: {error}</div>
  const hasData = data && (data.momentum != null || (Array.isArray(data.history) && data.history.length > 0))
  if (!hasData) return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No revision data</div>

  const momentum = data.momentum != null ? parseFloat(data.momentum) : null
  let ups = data.up_revisions
  let downs = data.down_revisions
  if (ups == null && downs == null && Array.isArray(data.history)) {
    ups = 0; downs = 0
    const h = data.history
    for (let i = 0; i < h.length - 1; i++) {
      const cur = parseFloat(h[i].eps_estimate)
      const prev = parseFloat(h[i + 1].eps_estimate)
      if (!Number.isFinite(cur) || !Number.isFinite(prev)) continue
      if (cur > prev) ups += 1
      else if (cur < prev) downs += 1
    }
  }
  ups = ups || 0
  downs = downs || 0
  const total = ups + downs
  const upPct = total > 0 ? (ups / total) * 100 : 0
  const downPct = total > 0 ? (downs / total) * 100 : 0
  const momTone = momentum == null ? 'dim' : momentum > 0.2 ? 'pos' : momentum < -0.2 ? 'neg' : 'warn'

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div className="grid c3" style={{ gap: 8 }}>
        <div className="tile">
          <div className="lab">up</div>
          <div className="v pos">{ups}</div>
        </div>
        <div className="tile">
          <div className="lab">down</div>
          <div className="v neg">{downs}</div>
        </div>
        <div className="tile">
          <div className="lab">momentum</div>
          <div className={`v ${momTone}`}>{momentum == null ? '—' : `${momentum > 0 ? '+' : ''}${momentum.toFixed(2)}`}</div>
        </div>
      </div>
      {total > 0 && (
        <div style={{ display: 'flex', height: 6, border: '1px solid var(--line)' }}>
          <div style={{ width: `${upPct}%`, background: 'var(--pos)' }} title={`${ups} up`} />
          <div style={{ width: `${downPct}%`, background: 'var(--neg)' }} title={`${downs} down`} />
        </div>
      )}
    </div>
  )
}
