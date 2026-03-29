import Panel from './Panel'

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
      <span className={`text-sm font-semibold ${connected ? 'text-profit' : 'text-loss'}`}>
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
  const statusColor = errors === 0 ? 'text-profit' : errors <= 5 ? 'text-warning' : 'text-loss'

  return (
    <div className={`bg-bg-primary border border-border border-l-2 ${borderColor} p-3`}>
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

export default function HealthTab({ health, loading }) {
  if (loading) {
    return (
      <Panel title="System Health">
        <div className="skeleton h-32 w-full" />
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
    : '--'

  // / cycle timing entries
  const cycleEntries = [
    { label: 'Analysis', ts: cycles.last_analysis },
    { label: 'Strategy Eval', ts: cycles.last_strategy_eval },
    { label: 'Evolution', ts: cycles.last_evolution },
    { label: 'Trade', ts: cycles.last_trade },
    { label: 'Synthesis', ts: cycles.last_synthesis },
  ]

  // / known source keys in display order
  const sourceKeys = ['groq', 'deepseek', 'edgar', 'finnhub', 'coingecko']
  // / include any extra sources from backend
  const allSourceKeys = [...new Set([...sourceKeys, ...Object.keys(sources)])]

  return (
    <div className="space-y-2">
      {/* row 1: db + connections + cycles */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        <Panel title="Database">
          <div className="space-y-3">
            <DbIndicator connected={health.db_connected} />
            <div className="flex items-center justify-between text-xs">
              <span className="text-text-secondary">Size</span>
              <span className="font-mono">
                {storage.db_size_mb != null ? `${storage.db_size_mb} MB` : '--'}
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
              <span className="font-mono">{connections.active ?? '--'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-secondary">Commits</span>
              <span className="font-mono">{connections.commits?.toLocaleString() ?? '--'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-secondary">Rollbacks</span>
              <span className="font-mono">{connections.rollbacks?.toLocaleString() ?? '--'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-secondary">Cache Hit</span>
              <span className={`font-mono ${connections.cache_hit_ratio >= 0.99 ? 'text-profit' : connections.cache_hit_ratio >= 0.95 ? 'text-warning' : 'text-loss'}`}>
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

      {/* row 2: source status cards */}
      <Panel title="Data Sources">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-2">
          {allSourceKeys.map(key => (
            <SourceCard key={key} name={key} source={sources[key]} />
          ))}
          {allSourceKeys.length === 0 && (
            <div className="text-text-muted text-sm col-span-full">No source data</div>
          )}
        </div>
      </Panel>

      {/* row 3: storage table breakdown */}
      {tables.length > 0 && (
        <Panel title="Storage Breakdown" collapsible defaultOpen={false}>
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
        </Panel>
      )}

      {/* row 4: recent errors */}
      <Panel title="Recent Errors" collapsible defaultOpen={errors.length > 0}>
        {errors.length > 0 ? (
          <div style={{ maxHeight: 320, overflowY: 'auto' }}>
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
                    <td className="px-2 py-1 font-mono">{e.symbol || '--'}</td>
                    <td className="px-2 py-1 text-loss truncate max-w-[300px]" title={e.message}>
                      {e.message}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-profit text-sm py-2">No recent errors</div>
        )}
      </Panel>
    </div>
  )
}
