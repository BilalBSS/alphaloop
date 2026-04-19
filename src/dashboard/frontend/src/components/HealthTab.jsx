import { useState, useMemo, Fragment } from 'react'
import Panel from './Panel'
import HeroBanner from './HeroBanner'
import EmptyState from './EmptyState'
import { useApi } from '../hooks/useApi'

// / relative time display
function timeAgo(ts) {
  if (!ts) return 'never'
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 0) return 'just now'
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ${mins % 60}m ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

// / db connection indicator
function DbIndicator({ connected }) {
  return (
    <div className="flex items-center gap-2">
      <div className={`w-3 h-3 rounded-full ${connected ? 'bg-profit' : 'bg-loss'}`} />
      <span className={`text-sm font-semibold ${connected ? 'pnl-positive' : 'pnl-negative'}`}>
        {connected ? 'Connected' : 'Disconnected'}
      </span>
    </div>
  )
}

// / source status card with error-count color coding
function SourceCard({ name, source }) {
  if (!source) return null
  const errors = source.errors_24h || 0
  const borderColor = errors === 0 ? 'border-l-profit' : errors <= 5 ? 'border-l-warning' : 'border-l-loss'
  const dotColor = errors === 0 ? 'bg-profit' : errors <= 5 ? 'bg-warning' : 'bg-loss'
  const statusText = errors === 0 ? 'healthy' : errors <= 5 ? 'degraded' : 'failing'
  const statusColor = errors === 0 ? 'pnl-positive' : errors <= 5 ? 'text-warning' : 'pnl-negative'

  return (
    <div className={`bg-bg-primary border border-border rounded border-l-2 ${borderColor} p-3`}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-semibold uppercase">{name}</span>
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full ${dotColor}`} />
          <span className={`text-[10px] uppercase ${statusColor}`}>{statusText}</span>
        </div>
      </div>
      <div className="text-xs text-text-secondary">
        <span className="font-mono">{errors}</span> errors (24h)
      </div>
      {source.last_error && (
        <div className="text-[10px] text-text-muted mt-1">
          Last error: {timeAgo(source.last_error)}
        </div>
      )}
    </div>
  )
}

// / classify a cost entry's provider bucket — groq / deepseek / cerebras / other
function providerBucket(source) {
  const s = (source || '').toLowerCase()
  if (s.includes('groq')) return 'Groq'
  if (s.includes('deepseek')) return 'DeepSeek'
  if (s.includes('cerebras')) return 'Cerebras'
  return 'Other'
}

// / aggregate cost rows by provider bucket; retain per-date detail for drilldown.
// / input row shape is flexible: { source, call_count, tokens_in, tokens_out, usd / estimated_cost_usd, date? }
function aggregateByProvider(rows) {
  const map = new Map()
  for (const r of rows) {
    const bucket = providerBucket(r.source)
    if (!map.has(bucket)) {
      map.set(bucket, { provider: bucket, calls: 0, tokens_in: 0, tokens_out: 0, usd: 0, detail: [] })
    }
    const agg = map.get(bucket)
    const usd = parseFloat(r.estimated_cost_usd ?? r.usd) || 0
    agg.calls += parseInt(r.call_count) || 0
    agg.tokens_in += parseInt(r.tokens_in) || 0
    agg.tokens_out += parseInt(r.tokens_out) || 0
    agg.usd += usd
    agg.detail.push({
      source: r.source,
      date: r.date || r.day || null,
      calls: parseInt(r.call_count) || 0,
      tokens_in: parseInt(r.tokens_in) || 0,
      tokens_out: parseInt(r.tokens_out) || 0,
      usd,
    })
  }
  // / ensure the three known providers always show, even if zero — makes Cerebras=0 visible
  for (const known of ['Groq', 'DeepSeek', 'Cerebras']) {
    if (!map.has(known)) {
      map.set(known, { provider: known, calls: 0, tokens_in: 0, tokens_out: 0, usd: 0, detail: [] })
    }
  }
  return Array.from(map.values()).sort((a, b) => b.usd - a.usd)
}

// / LLM + data API cost panel — aggregate by provider, click to expand per-date
function CostPanel() {
  const { data, loading, error } = useApi('/api/costs', 60000)
  const [expanded, setExpanded] = useState({})

  if (loading && !data) return <div className="skeleton h-24 w-full rounded" />
  if (error) return <div className="pnl-negative text-sm py-2">Failed to load: {error}</div>
  if (!data) return <EmptyState title="No cost data" hint="Cost tracking populates on the first LLM call." />

  const providers = Array.isArray(data)
    ? data
    : (data.costs || data.providers || [])
  if (providers.length === 0) {
    return <EmptyState title="No cost entries yet" hint="First LLM call writes a row to api_costs — check the LLM client config if calls aren't landing." />
  }

  const aggregated = aggregateByProvider(providers)
  const totalUsd = data.total_usd != null
    ? parseFloat(data.total_usd) || 0
    : aggregated.reduce((s, p) => s + p.usd, 0)
  const totalCalls = aggregated.reduce((s, p) => s + p.calls, 0)
  const totalTokensIn = aggregated.reduce((s, p) => s + p.tokens_in, 0)
  const totalTokensOut = aggregated.reduce((s, p) => s + p.tokens_out, 0)
  const period = data.period || 'recent activity'

  const fmtTokens = (n) => n > 0 ? (n / 1000).toFixed(1) + 'k' : '—'

  return (
    <div>
      <div className="type-metric-label mb-2">Period: {period}</div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary text-[11px] uppercase border-b border-border">
            <th className="text-left px-2 py-2 w-6"></th>
            <th className="text-left px-2 py-2">Provider</th>
            <th className="text-right px-2 py-2">Calls</th>
            <th className="text-right px-2 py-2">In</th>
            <th className="text-right px-2 py-2">Out</th>
            <th className="text-right px-2 py-2">USD</th>
          </tr>
        </thead>
        <tbody>
          {aggregated.map((p) => {
            const isOpen = expanded[p.provider]
            const hasDetail = p.detail.length > 0
            const zeroCalls = p.calls === 0
            return (
              <Fragment key={p.provider}>
                <tr
                  onClick={() => hasDetail && setExpanded({ ...expanded, [p.provider]: !isOpen })}
                  className={`border-t border-border ${hasDetail ? 'cursor-pointer hover:bg-bg-hover' : ''}`}
                  style={{ height: 36 }}
                >
                  <td className="px-2 py-1 text-text-muted text-[10px]">
                    {hasDetail ? (isOpen ? '▾' : '▸') : ''}
                  </td>
                  <td className="px-2 py-1 font-mono font-semibold">
                    {p.provider}
                    {zeroCalls && <span className="ml-2 chip chip-warning">no calls</span>}
                  </td>
                  <td className="px-2 py-1 text-right font-mono text-text-secondary">{p.calls.toLocaleString()}</td>
                  <td className="px-2 py-1 text-right font-mono text-text-muted">{fmtTokens(p.tokens_in)}</td>
                  <td className="px-2 py-1 text-right font-mono text-text-muted">{fmtTokens(p.tokens_out)}</td>
                  <td className={`px-2 py-1 text-right font-mono font-semibold ${p.usd > 1 ? 'text-warning' : 'text-text-primary'}`}>
                    ${p.usd.toFixed(4)}
                  </td>
                </tr>
                {isOpen && p.detail.map((d, di) => (
                  <tr key={`${p.provider}-d-${di}`} className="border-t border-border/30 bg-bg-primary/40 text-[11px]">
                    <td></td>
                    <td className="px-2 py-1 pl-6 font-mono text-text-muted">
                      {d.date ? d.date.split('T')[0] : d.source || '—'}
                      {d.source && d.date && <span className="ml-2 text-text-muted">{d.source}</span>}
                    </td>
                    <td className="px-2 py-1 text-right font-mono text-text-secondary">{d.calls.toLocaleString()}</td>
                    <td className="px-2 py-1 text-right font-mono text-text-muted">{fmtTokens(d.tokens_in)}</td>
                    <td className="px-2 py-1 text-right font-mono text-text-muted">{fmtTokens(d.tokens_out)}</td>
                    <td className="px-2 py-1 text-right font-mono text-text-muted">${d.usd.toFixed(4)}</td>
                  </tr>
                ))}
              </Fragment>
            )
          })}
          {/* totals footer */}
          <tr className="border-t-2 border-accent bg-bg-primary" style={{ height: 36 }}>
            <td></td>
            <td className="px-2 py-1 font-mono font-semibold uppercase text-[10px]">Total</td>
            <td className="px-2 py-1 text-right font-mono font-semibold">{totalCalls.toLocaleString()}</td>
            <td className="px-2 py-1 text-right font-mono text-text-secondary">{fmtTokens(totalTokensIn)}</td>
            <td className="px-2 py-1 text-right font-mono text-text-secondary">{fmtTokens(totalTokensOut)}</td>
            <td className={`px-2 py-1 text-right font-mono font-bold ${totalUsd > 5 ? 'pnl-negative' : totalUsd > 1 ? 'text-warning' : 'pnl-positive'}`}>
              ${totalUsd.toFixed(4)}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

// / derive a freshness status tier from the staleness_monitor payload
function stalenessStatus(item) {
  if (item.status) return item.status
  if (item.is_stale) return 'red'
  const s = parseFloat(item.staleness_hours)
  const t = parseFloat(item.threshold_hours)
  if (!Number.isFinite(s) || !Number.isFinite(t) || t <= 0) return 'unknown'
  return s / t > 0.6 ? 'yellow' : 'green'
}

// / individual staleness tile — colored by freshness status
function StalenessTile({ item, onClick }) {
  const status = stalenessStatus(item)
  const color = {
    green: { bg: 'bg-profit/10', border: 'border-profit/40', dot: 'bg-profit', text: 'pnl-positive' },
    yellow: { bg: 'bg-warning/10', border: 'border-warning/40', dot: 'bg-warning', text: 'text-warning' },
    red: { bg: 'bg-loss/10', border: 'border-loss/40', dot: 'bg-loss', text: 'pnl-negative' },
    unknown: { bg: 'bg-bg-primary', border: 'border-border', dot: 'bg-text-muted', text: 'text-text-muted' },
  }[status] || { bg: 'bg-bg-primary', border: 'border-border', dot: 'bg-text-muted', text: 'text-text-muted' }
  const errs = item.error_count_24h || 0
  return (
    <button
      onClick={() => onClick && onClick(item)}
      className={`${color.bg} border ${color.border} rounded p-2 text-left hover:bg-bg-hover transition-colors`}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-semibold uppercase font-mono truncate">{item.source}</span>
        <div className={`w-2 h-2 rounded-full ${color.dot}`} />
      </div>
      <div className="text-[10px] text-text-secondary">
        {timeAgo(item.last_update)}
      </div>
      {errs > 0 && (
        <div className={`text-[10px] ${color.text}`}>
          {errs} err (24h)
        </div>
      )}
    </button>
  )
}

function StalenessPanel() {
  const { data, loading, error } = useApi('/api/staleness', 60000)
  const [selected, setSelected] = useState(null)

  if (loading && !data) return <div className="skeleton h-24 w-full rounded" />
  if (error) return <div className="pnl-negative text-sm py-2">Failed to load: {error}</div>
  const sources = Array.isArray(data) ? data : (data?.sources || [])
  if (sources.length === 0) {
    return <EmptyState title="No staleness data" hint="Staleness monitor writes a row per data source on each refresh." />
  }

  const counts = { green: 0, yellow: 0, red: 0, unknown: 0 }
  for (const d of sources) {
    const s = stalenessStatus(d)
    counts[s] = (counts[s] || 0) + 1
  }

  return (
    <div>
      <div className="flex gap-3 mb-3 text-[10px] text-text-muted">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-profit" />{counts.green} fresh</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-warning" />{counts.yellow} stale</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-loss" />{counts.red} critical</span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2">
        {sources.map((item, i) => (
          <StalenessTile key={`${item.source}-${i}`} item={item} onClick={setSelected} />
        ))}
      </div>
      {selected && (
        <div className="mt-3 bg-bg-primary border border-accent rounded p-3 text-xs">
          <div className="flex items-center justify-between mb-2">
            <span className="font-mono font-semibold uppercase">{selected.source}</span>
            <button onClick={() => setSelected(null)} className="text-text-muted hover:text-text-primary">✕</button>
          </div>
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div>
              <div className="type-metric-label">Status</div>
              <div className="font-semibold uppercase">{stalenessStatus(selected)}</div>
            </div>
            <div>
              <div className="type-metric-label">Last Update</div>
              <div className="font-mono">{timeAgo(selected.last_update)}</div>
            </div>
            <div>
              <div className="type-metric-label">Staleness</div>
              <div className="font-mono">{selected.staleness_hours != null ? `${selected.staleness_hours}h` : '—'}</div>
            </div>
            <div>
              <div className="type-metric-label">Threshold</div>
              <div className="font-mono">{selected.threshold_hours != null ? `${selected.threshold_hours}h` : '—'}</div>
            </div>
            {selected.last_error && (
              <div className="col-span-2">
                <div className="type-metric-label">Last Error</div>
                <div className="pnl-negative">{selected.last_error}</div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// / hero — system status aggregate derived from health payload
function HealthHero({ health }) {
  const errors = health?.recent_errors || []
  const lastError = errors[0]?.timestamp || null
  const dbOk = health?.db_connected
  const sources = health?.sources || {}
  const sourceErrors = Object.values(sources).reduce((s, v) => s + (v?.errors_24h || 0), 0)
  const status = !dbOk ? 'degraded' : sourceErrors > 10 ? 'degraded' : 'ok'
  const statusClass = status === 'ok' ? 'pnl-positive' : 'text-warning'
  const uptimeHours = health?.uptime_seconds != null ? (health.uptime_seconds / 3600).toFixed(1) : null

  return (
    <HeroBanner>
      <div className="hero-metric">
        <span className="hero-metric-label">System status</span>
        <span className={`hero-metric-value font-mono ${statusClass}`}>
          {status === 'ok' ? 'OK' : 'DEGRADED'}
        </span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">DB</span>
        <span className={`hero-metric-value-sm font-mono ${dbOk ? 'pnl-positive' : 'pnl-negative'}`}>
          {dbOk ? 'connected' : 'disconnected'}
        </span>
      </div>
      <div className="hero-metric">
        <span className="hero-metric-label">Source errors (24h)</span>
        <span className={`hero-metric-value-sm font-mono ${sourceErrors === 0 ? 'pnl-positive' : sourceErrors < 10 ? 'text-warning' : 'pnl-negative'}`}>
          {sourceErrors}
        </span>
      </div>
      {uptimeHours && (
        <div className="hero-metric">
          <span className="hero-metric-label">Uptime</span>
          <span className="hero-metric-value-sm font-mono">{uptimeHours}h</span>
        </div>
      )}
      <div className="hero-metric">
        <span className="hero-metric-label">Last error</span>
        <span className="hero-metric-value-sm font-mono text-text-primary">
          {lastError ? timeAgo(lastError) : 'none'}
        </span>
      </div>
    </HeroBanner>
  )
}

export default function HealthTab({ health, loading }) {
  if (loading) {
    return (
      <Panel title="System Health">
        <div className="skeleton h-32 w-full rounded" />
      </Panel>
    )
  }

  if (!health) {
    return <Panel title="System Health" error="Health data unavailable" />
  }

  const storage = health.storage || {}
  const connections = health.connections || {}
  const cycles = health.cycles || {}
  const sources = health.sources || {}
  const errors = health.recent_errors || []
  const tables = storage.tables || []

  const cacheHitPct = connections.cache_hit_ratio
    ? (connections.cache_hit_ratio * 100).toFixed(2) + '%'
    : '—'

  // / cycle timing entries
  const cycleEntries = [
    { label: 'Analysis', ts: cycles.last_analysis },
    { label: 'Strategy Eval', ts: cycles.last_strategy_eval },
    { label: 'Evolution', ts: cycles.last_evolution },
    { label: 'Trade', ts: cycles.last_trade },
    { label: 'Synthesis', ts: cycles.last_synthesis },
  ]

  const sourceKeys = ['groq', 'deepseek', 'edgar', 'finnhub', 'coingecko']
  const allSourceKeys = [...new Set([...sourceKeys, ...Object.keys(sources)])]

  return (
    <div className="space-y-6">
      <HealthHero health={health} />

      {/* row 1: db + connections + cycles */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Panel title="Database">
          <div className="space-y-3">
            <DbIndicator connected={health.db_connected} />
            <div className="flex items-center justify-between text-xs">
              <span className="text-text-secondary">Size</span>
              <span className="font-mono">
                {storage.db_size_mb != null ? `${storage.db_size_mb} MB` : '—'}
              </span>
            </div>
            {storage.db_size_mb != null && (
              <div className="w-full bg-bg-primary rounded h-2">
                <div
                  className="h-2 rounded bg-accent"
                  style={{ width: `${Math.min((storage.db_size_mb / 512) * 100, 100)}%` }}
                />
              </div>
            )}
          </div>
        </Panel>

        <Panel title="Connections">
          <div className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span className="text-text-secondary">Active</span>
              <span className="font-mono">{connections.active ?? '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-secondary">Commits</span>
              <span className="font-mono">{connections.commits?.toLocaleString() ?? '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-secondary">Rollbacks</span>
              <span className="font-mono">{connections.rollbacks?.toLocaleString() ?? '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-secondary">Cache Hit</span>
              <span className={`font-mono ${connections.cache_hit_ratio >= 0.99 ? 'pnl-positive' : connections.cache_hit_ratio >= 0.95 ? 'text-warning' : 'pnl-negative'}`}>
                {cacheHitPct}
              </span>
            </div>
          </div>
        </Panel>

        <Panel title="Cycle Timings">
          <div className="space-y-2 text-xs">
            {cycleEntries.map(c => (
              <div key={c.label} className="flex justify-between">
                <span className="text-text-secondary">{c.label}</span>
                <span className="font-mono text-text-primary">{timeAgo(c.ts)}</span>
              </div>
            ))}
            {cycles.symbols_today != null && (
              <div className="flex justify-between pt-1 border-t border-border">
                <span className="text-text-secondary">Symbols Today</span>
                <span className="font-mono text-accent">{cycles.symbols_today}</span>
              </div>
            )}
          </div>
        </Panel>
      </div>

      <Panel title="Data Freshness">
        <StalenessPanel />
      </Panel>

      <Panel title="API Costs">
        <CostPanel />
      </Panel>

      <Panel title="Source Error Summary" collapsible defaultOpen={false}>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
          {allSourceKeys.map(key => (
            <SourceCard key={key} name={key} source={sources[key]} />
          ))}
          {allSourceKeys.length === 0 && (
            <div className="text-text-muted text-sm col-span-full">No source data</div>
          )}
        </div>
      </Panel>

      {tables.length > 0 && (
        <Panel title="Storage Breakdown" collapsible defaultOpen={false}>
          <div className="overflow-x-auto table-scroll-fade">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-secondary text-[11px] uppercase">
                  <th className="text-left px-2 py-1">Table</th>
                  <th className="text-right px-2 py-1">Size (MB)</th>
                  <th className="text-right px-2 py-1">Rows</th>
                </tr>
              </thead>
              <tbody>
                {tables.map(t => (
                  <tr key={t.name} className="border-t border-border" style={{ height: 28 }}>
                    <td className="px-2 py-1 font-mono">{t.name}</td>
                    <td className="px-2 py-1 text-right font-mono">{t.size_mb}</td>
                    <td className="px-2 py-1 text-right font-mono">{t.rows?.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <Panel title="Recent Errors" collapsible defaultOpen={errors.length > 0}>
        {errors.length > 0 ? (
          <div className="overflow-x-auto table-scroll-fade" style={{ maxHeight: 320, overflowY: 'auto' }}>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-secondary text-[11px] uppercase">
                  <th className="text-left px-2 py-1">Time</th>
                  <th className="text-left px-2 py-1">Source</th>
                  <th className="text-left px-2 py-1">Symbol</th>
                  <th className="text-left px-2 py-1">Message</th>
                </tr>
              </thead>
              <tbody>
                {errors.slice(0, 20).map((e, i) => (
                  <tr key={i} className="border-t border-border" style={{ height: 28 }}>
                    <td className="px-2 py-1 text-text-muted whitespace-nowrap">
                      {timeAgo(e.timestamp)}
                    </td>
                    <td className="px-2 py-1 font-mono uppercase">{e.source}</td>
                    <td className="px-2 py-1 font-mono">{e.symbol || '—'}</td>
                    <td className="px-2 py-1 pnl-negative truncate max-w-[300px]" title={e.message}>
                      {e.message}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="pnl-positive text-sm py-2">No recent errors</div>
        )}
      </Panel>
    </div>
  )
}
