import { useApi } from '../../hooks/useApi'
import TimeSeriesLineChart from '../chart/TimeSeriesLineChart'

// / directional arrow vs prior reading (used by macro card)
function DirArrow({ current, prior }) {
  if (current == null || prior == null) return null
  const c = parseFloat(current)
  const p = parseFloat(prior)
  if (!Number.isFinite(c) || !Number.isFinite(p)) return null
  if (c > p) return <span className="text-profit ml-1">▲</span>
  if (c < p) return <span className="text-loss ml-1">▼</span>
  return <span className="text-text-muted ml-1">━</span>
}

// / macro regime card: DFF, CPI, UNRATE, yield spread
export function MacroRegimeCard() {
  const { data, loading, error } = useApi('/api/macro-context', 300000)

  if (loading && !data) return <div className="skeleton h-24 w-full" />
  if (error) return <div className="text-loss text-sm py-2">Failed to load macro: {error}</div>
  if (!data) return <div className="text-text-muted text-sm py-2">No macro data</div>

  const prior = data.prior || {}
  const rows = [
    { key: 'dff', label: 'Fed Funds', fmt: v => `${parseFloat(v).toFixed(2)}%` },
    { key: 'cpi', label: 'CPI YoY', fmt: v => `${parseFloat(v).toFixed(2)}%` },
    { key: 'unrate', label: 'Unemployment', fmt: v => `${parseFloat(v).toFixed(1)}%` },
    { key: 'yield_spread', label: '10Y-2Y Spread', fmt: v => `${parseFloat(v).toFixed(2)}%` },
  ]

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {rows.map(r => {
          const v = data[r.key]
          const p = prior[r.key]
          if (v == null) return (
            <div key={r.key} className="bg-bg-primary border border-border p-2">
              <div className="text-[10px] uppercase text-text-muted">{r.label}</div>
              <div className="text-lg font-mono text-text-muted">--</div>
            </div>
          )
          return (
            <div key={r.key} className="bg-bg-primary border border-border p-2">
              <div className="text-[10px] uppercase text-text-muted">{r.label}</div>
              <div className="text-lg font-mono font-semibold text-text-primary">
                {r.fmt(v)}
                <DirArrow current={v} prior={p} />
              </div>
            </div>
          )
        })}
      </div>
      {data.updated_at && (
        <div className="text-[10px] text-text-muted">Updated: {data.updated_at.replace('T', ' ').slice(0, 16)}</div>
      )}
    </div>
  )
}

// / analyst consensus gauge + rating distribution stacked bar
export function AnalystConsensusCard({ symbol }) {
  const { data, loading, error } = useApi(`/api/analyst-ratings/${symbol}`, 300000)

  if (loading && !data) return <div className="skeleton h-24 w-full" />
  if (error) return <div className="text-loss text-sm py-2">Failed to load: {error}</div>
  // / backend returns { history: [{...latest, consensus_score}] }; accept flat or history[0]
  const latest = data?.consensus_score != null ? data : (Array.isArray(data?.history) ? data.history[0] : null)
  if (!latest || latest.consensus_score == null) {
    return <div className="text-text-muted text-sm py-2">No analyst ratings</div>
  }
  const score = parseFloat(latest.consensus_score) || 0
  // / map [-1, +1] → [0, 100] percentage for gauge
  const pct = ((score + 1) / 2) * 100
  const color = score >= 0.4 ? 'text-profit' : score <= -0.4 ? 'text-loss' : 'text-warning'
  const label = score >= 0.6 ? 'Strong Buy' : score >= 0.2 ? 'Buy' : score > -0.2 ? 'Hold' : score > -0.6 ? 'Sell' : 'Strong Sell'

  const dist = [
    { key: 'strong_buy', label: 'SB', count: latest.strong_buy || 0, color: 'bg-profit' },
    { key: 'buy', label: 'B', count: latest.buy || 0, color: 'bg-profit/60' },
    { key: 'hold', label: 'H', count: latest.hold || 0, color: 'bg-warning' },
    { key: 'sell', label: 'S', count: latest.sell || 0, color: 'bg-loss/60' },
    { key: 'strong_sell', label: 'SS', count: latest.strong_sell || 0, color: 'bg-loss' },
  ]
  const totalCount = dist.reduce((s, d) => s + d.count, 0)

  return (
    <div className="space-y-3">
      {/* gauge bar from -1 to +1 */}
      <div>
        <div className="flex justify-between text-[10px] text-text-muted mb-1">
          <span>Strong Sell</span>
          <span>Hold</span>
          <span>Strong Buy</span>
        </div>
        <div className="relative h-3 bg-bg-primary border border-border rounded">
          <div className="absolute top-0 h-full w-0.5 bg-text-muted" style={{ left: '50%' }} />
          <div className="absolute top-0 h-full w-1 bg-accent" style={{ left: `calc(${pct}% - 2px)` }} />
        </div>
        <div className="flex items-baseline gap-2 mt-1">
          <span className={`text-xl font-mono font-bold ${color}`}>{score.toFixed(2)}</span>
          <span className={`text-xs uppercase font-semibold ${color}`}>{label}</span>
        </div>
      </div>

      {/* stacked distribution */}
      {totalCount > 0 && (
        <div>
          <div className="text-[10px] uppercase text-text-muted mb-1">Analysts: {totalCount}</div>
          <div className="flex h-4 w-full overflow-hidden border border-border rounded">
            {dist.map(d => {
              const w = (d.count / totalCount) * 100
              if (w === 0) return null
              return (
                <div
                  key={d.key}
                  className={d.color}
                  style={{ width: `${w}%` }}
                  title={`${d.label}: ${d.count}`}
                />
              )
            })}
          </div>
          <div className="flex justify-between text-[10px] text-text-muted mt-1 font-mono">
            {dist.map(d => (
              <span key={d.key}>{d.label}:{d.count}</span>
            ))}
          </div>
        </div>
      )}

      {latest.target_mean != null && (
        <div className="flex items-center justify-between text-xs">
          <span className="text-text-secondary uppercase text-[10px]">Avg Target</span>
          <span className="font-mono font-semibold">${parseFloat(latest.target_mean).toFixed(2)}</span>
        </div>
      )}
    </div>
  )
}

// / options flow: iv rank + put/call ratio + max pain
export function OptionsFlowCard({ symbol }) {
  const { data, loading, error } = useApi(`/api/options/${symbol}`, 300000)

  if (loading && !data) return <div className="skeleton h-24 w-full" />
  if (error) return <div className="text-loss text-sm py-2">Failed to load: {error}</div>
  // / backend: { history: [...], latest }. legacy flat shape still accepted.
  const latest = data?.latest ?? (Array.isArray(data?.history) ? data.history[0] : data)
  if (!latest || (latest.iv_rank == null && latest.put_call_ratio == null && latest.max_pain == null)) {
    return <div className="text-text-muted text-sm py-2">No options data</div>
  }

  // / backend stores iv_rank as 0..1 fraction; earlier draft emitted 0..100 directly
  const ivRaw = latest.iv_rank != null ? parseFloat(latest.iv_rank) : null
  const ivRank = ivRaw == null ? null : (ivRaw <= 1 ? ivRaw * 100 : ivRaw)
  const pcr = latest.put_call_ratio != null ? parseFloat(latest.put_call_ratio) : null
  const maxPain = latest.max_pain != null ? parseFloat(latest.max_pain) : null

  const ivColor = ivRank == null ? 'text-text-muted' : ivRank > 70 ? 'text-loss' : ivRank > 40 ? 'text-warning' : 'text-profit'
  const pcrColor = pcr == null ? 'text-text-muted' : pcr > 1.2 ? 'text-loss' : pcr > 0.8 ? 'text-warning' : 'text-profit'

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-bg-primary border border-border p-2">
          <div className="text-[10px] uppercase text-text-muted">IV Rank</div>
          <div className={`text-xl font-mono font-bold ${ivColor}`}>
            {ivRank == null ? '--' : `${ivRank.toFixed(0)}%`}
          </div>
          {/* horizontal gauge */}
          {ivRank != null && (
            <div className="mt-1 h-1.5 bg-bg-surface rounded overflow-hidden">
              <div
                className={ivRank > 70 ? 'bg-loss h-full' : ivRank > 40 ? 'bg-warning h-full' : 'bg-profit h-full'}
                style={{ width: `${Math.min(100, ivRank)}%` }}
              />
            </div>
          )}
        </div>
        <div className="bg-bg-primary border border-border p-2">
          <div className="text-[10px] uppercase text-text-muted">Put/Call</div>
          <div className={`text-xl font-mono font-bold ${pcrColor}`}>
            {pcr == null ? '--' : pcr.toFixed(2)}
          </div>
          <div className="text-[10px] text-text-muted mt-1">
            {pcr != null && (pcr > 1.2 ? 'bearish' : pcr > 0.8 ? 'neutral' : 'bullish')}
          </div>
        </div>
        <div className="bg-bg-primary border border-border p-2">
          <div className="text-[10px] uppercase text-text-muted">Max Pain</div>
          <div className="text-xl font-mono font-bold text-text-primary">
            {maxPain == null ? '--' : `$${maxPain.toFixed(0)}`}
          </div>
        </div>
      </div>
      {Array.isArray(data.history) && data.history.length > 0 && (
        <div>
          <div className="text-[10px] uppercase text-text-muted mb-1">IV Rank Trend</div>
          <TimeSeriesLineChart
            data={data.history}
            timeKey="date"
            valueKey="iv_rank"
            color="#f59e0b"
            height={80}
            valueFmt={v => `${v.toFixed(0)}%`}
            emptyText="--"
          />
        </div>
      )}
    </div>
  )
}

// / dark pool: latest ratio + weekly trend sparkline
export function DarkPoolCard({ symbol }) {
  const { data, loading, error } = useApi(`/api/dark-pool/${symbol}`, 300000)

  if (loading && !data) return <div className="skeleton h-24 w-full" />
  if (error) return <div className="text-loss text-sm py-2">Failed to load: {error}</div>
  // / backend: { history: [...], latest: { dark_pool_ratio, ats_volume, total_volume, week_start } }
  const latest = data?.latest ?? (Array.isArray(data?.history) ? data.history[0] : data)
  const rawRatio = latest?.dark_pool_ratio ?? latest?.latest_ratio
  if (!latest || rawRatio == null) {
    return <div className="text-text-muted text-sm py-2">No dark pool data</div>
  }

  const ratio = parseFloat(rawRatio) || 0
  const pct = ratio * 100
  // / >45% suggests institutional accumulation
  const color = pct > 45 ? 'text-accent' : pct > 35 ? 'text-warning' : 'text-text-secondary'
  const label = pct > 45 ? 'Heavy Institutional' : pct > 35 ? 'Elevated' : 'Normal'

  return (
    <div className="space-y-2">
      <div className="bg-bg-primary border border-border p-2">
        <div className="text-[10px] uppercase text-text-muted">Latest Ratio</div>
        <div className="flex items-baseline gap-2">
          <span className={`text-2xl font-mono font-bold ${color}`}>{pct.toFixed(1)}%</span>
          <span className={`text-[10px] uppercase font-semibold ${color}`}>{label}</span>
        </div>
        <div className="mt-1 h-1.5 bg-bg-surface rounded overflow-hidden">
          <div
            className={pct > 45 ? 'bg-accent h-full' : pct > 35 ? 'bg-warning h-full' : 'bg-text-secondary h-full'}
            style={{ width: `${Math.min(100, pct)}%` }}
          />
        </div>
      </div>
      {Array.isArray(data?.history) && data.history.length > 0 && (
        <div>
          <div className="text-[10px] uppercase text-text-muted mb-1">Weekly Trend</div>
          <TimeSeriesLineChart
            data={[...data.history].reverse()}
            timeKey="week_start"
            valueKey="dark_pool_ratio"
            color="#3b82f6"
            height={80}
            valueFmt={v => `${(v * 100).toFixed(1)}%`}
            emptyText="--"
          />
        </div>
      )}
    </div>
  )
}

// / congressional activity: ratio + trades + bull/bear pie
export function CongressionalCard({ symbol }) {
  const { data, loading, error } = useApi(`/api/congressional/${symbol}`, 300000)

  if (loading && !data) return <div className="skeleton h-24 w-full" />
  if (error) return <div className="text-loss text-sm py-2">Failed to load: {error}</div>
  if (!data || (!data.trades || data.trades.length === 0)) {
    return <div className="text-text-muted text-sm py-2">No congressional trades</div>
  }

  const trades = data.trades || []
  const ratioRaw = data.net_buy_ratio ?? data.ratio
  const ratio = ratioRaw != null ? parseFloat(ratioRaw) : null
  const buys = trades.filter(t => (t.transaction_type || t.side || '').toLowerCase().includes('buy')).length
  const sells = trades.length - buys
  const total = buys + sells
  const buyPct = total > 0 ? (buys / total) * 100 : 0
  const sellPct = total > 0 ? (sells / total) * 100 : 0
  const ratioColor = ratio == null ? 'text-text-muted' : ratio > 0.6 ? 'text-profit' : ratio < 0.4 ? 'text-loss' : 'text-warning'

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-bg-primary border border-border p-2">
          <div className="text-[10px] uppercase text-text-muted">Buy Ratio</div>
          <div className={`text-2xl font-mono font-bold ${ratioColor}`}>
            {ratio == null ? '--' : ratio.toFixed(2)}
          </div>
          <div className="text-[10px] text-text-muted">
            {buys} buys / {sells} sells
          </div>
        </div>
        <div className="bg-bg-primary border border-border p-2">
          <div className="text-[10px] uppercase text-text-muted mb-1">Buy/Sell Split</div>
          <div className="flex h-6 w-full overflow-hidden rounded border border-border">
            {buys > 0 && (
              <div className="bg-profit" style={{ width: `${buyPct}%` }} title={`${buys} buys`} />
            )}
            {sells > 0 && (
              <div className="bg-loss" style={{ width: `${sellPct}%` }} title={`${sells} sells`} />
            )}
          </div>
          <div className="flex justify-between text-[10px] font-mono mt-1">
            <span className="text-profit">{buyPct.toFixed(0)}%</span>
            <span className="text-loss">{sellPct.toFixed(0)}%</span>
          </div>
        </div>
      </div>

      <div style={{ maxHeight: 180, overflowY: 'auto' }}>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-text-secondary text-[11px] uppercase">
              <th className="text-left px-2 py-1">Date</th>
              <th className="text-left px-2 py-1">Member</th>
              <th className="text-left px-2 py-1">Type</th>
              <th className="text-right px-2 py-1">Amount</th>
            </tr>
          </thead>
          <tbody>
            {trades.slice(0, 10).map((t, i) => {
              const type = (t.transaction_type || t.side || '--').toString()
              const isBuy = type.toLowerCase().includes('buy')
              return (
                <tr key={i} className={`border-t border-border border-l-2 ${isBuy ? 'border-l-profit' : 'border-l-loss'}`} style={{ height: 28 }}>
                  <td className="px-2 py-1 text-text-muted whitespace-nowrap">
                    {t.filing_date?.split('T')[0] || t.date?.split('T')[0] || '--'}
                  </td>
                  <td className="px-2 py-1 truncate max-w-[120px]" title={t.member || t.name}>
                    {t.member || t.name || '--'}
                  </td>
                  <td className={`px-2 py-1 uppercase text-[10px] font-semibold ${isBuy ? 'text-profit' : 'text-loss'}`}>
                    {type}
                  </td>
                  <td className="px-2 py-1 text-right font-mono">
                    {t.amount_range || t.amount || '--'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// / short squeeze risk: days-to-cover ratio + trend. short_ratio >= 5 is typically tight.
export function ShortSqueezeCard({ symbol }) {
  const { data, loading, error } = useApi(`/api/short/${symbol}`, 300000)

  if (loading && !data) return <div className="skeleton h-24 w-full" />
  if (error) return <div className="text-loss text-sm py-2">Failed to load: {error}</div>
  // / backend: { history: [{short_volume, total_volume, short_ratio}], latest }
  const latest = data?.latest ?? (Array.isArray(data?.history) ? data.history[0] : data)
  const rawRatio = latest?.short_ratio ?? latest?.short_pct_float
  if (!latest || rawRatio == null) {
    return <div className="text-text-muted text-sm py-2">No short interest data</div>
  }

  const ratio = parseFloat(rawRatio) || 0
  // / days-to-cover thresholds: <2 low, 2-5 normal, 5-10 elevated, >10 squeeze-risk
  const color = ratio > 10 ? 'text-loss' : ratio > 5 ? 'text-warning' : 'text-text-secondary'
  const label = ratio > 10 ? 'Squeeze Risk' : ratio > 5 ? 'Elevated' : 'Normal'

  return (
    <div className="space-y-2">
      <div className="bg-bg-primary border border-border p-2">
        <div className="text-[10px] uppercase text-text-muted">Days to Cover</div>
        <div className="flex items-baseline gap-2">
          <span className={`text-2xl font-mono font-bold ${color}`}>{ratio.toFixed(2)}</span>
          <span className={`text-[10px] uppercase font-semibold ${color}`}>{label}</span>
        </div>
        <div className="mt-1 h-1.5 bg-bg-surface rounded overflow-hidden">
          <div
            className={ratio > 10 ? 'bg-loss h-full' : ratio > 5 ? 'bg-warning h-full' : 'bg-text-secondary h-full'}
            style={{ width: `${Math.min(100, ratio * 8)}%` }}
          />
        </div>
        {latest.short_volume != null && (
          <div className="text-[10px] text-text-muted mt-1">
            Short vol: {Number(latest.short_volume).toLocaleString()}
          </div>
        )}
      </div>
      {Array.isArray(data?.history) && data.history.length > 0 && (
        <div>
          <div className="text-[10px] uppercase text-text-muted mb-1">Trend</div>
          <TimeSeriesLineChart
            data={[...data.history].reverse()}
            timeKey="date"
            valueKey="short_ratio"
            color="#f59e0b"
            height={80}
            valueFmt={v => `${v.toFixed(2)}`}
            emptyText="--"
          />
        </div>
      )}
    </div>
  )
}

// / earnings revisions: up/down counts + momentum score
export function EarningsRevisionsCard({ symbol }) {
  const { data, loading, error } = useApi(`/api/earnings-revisions/${symbol}`, 300000)

  if (loading && !data) return <div className="skeleton h-24 w-full" />
  if (error) return <div className="text-loss text-sm py-2">Failed to load: {error}</div>
  const hasData = data && (data.momentum != null || (Array.isArray(data.history) && data.history.length > 0))
  if (!hasData) {
    return <div className="text-text-muted text-sm py-2">No revision data</div>
  }

  const momentum = data.momentum != null ? parseFloat(data.momentum) : null
  // / derive up/down counts from eps_estimate deltas in history (newest-first)
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
  const momColor = momentum == null ? 'text-text-muted' : momentum > 0.2 ? 'text-profit' : momentum < -0.2 ? 'text-loss' : 'text-warning'

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-bg-primary border border-border p-2">
          <div className="text-[10px] uppercase text-text-muted">Up Revisions</div>
          <div className="text-xl font-mono font-bold text-profit">{ups}</div>
        </div>
        <div className="bg-bg-primary border border-border p-2">
          <div className="text-[10px] uppercase text-text-muted">Down Revisions</div>
          <div className="text-xl font-mono font-bold text-loss">{downs}</div>
        </div>
        <div className="bg-bg-primary border border-border p-2">
          <div className="text-[10px] uppercase text-text-muted">Momentum</div>
          <div className={`text-xl font-mono font-bold ${momColor}`}>
            {momentum == null ? '--' : (momentum > 0 ? '+' : '') + momentum.toFixed(2)}
          </div>
        </div>
      </div>
      {total > 0 && (
        <div className="flex h-3 w-full overflow-hidden rounded border border-border">
          <div className="bg-profit" style={{ width: `${upPct}%` }} title={`${ups} up`} />
          <div className="bg-loss" style={{ width: `${downPct}%` }} title={`${downs} down`} />
        </div>
      )}
    </div>
  )
}
