import Panel from './Panel'

function StatusDot({ ok }) {
  return (
    <div className={`w-2.5 h-2.5 rounded-full ${ok ? 'bg-profit' : 'bg-loss'}`} />
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

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
      <Panel title="Connections">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm">Database</span>
            <StatusDot ok={health.db_connected} />
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm">Storage</span>
            <span className="font-mono text-xs text-text-secondary">
              {health.storage_mb} / 512 MB
            </span>
          </div>
          <div className="w-full bg-bg-primary rounded h-2 mt-1">
            <div
              className="h-2 rounded bg-accent"
              style={{ width: `${Math.min((health.storage_mb / 512) * 100, 100)}%` }}
            />
          </div>
        </div>
      </Panel>

      <Panel title="Last Activity">
        <div className="space-y-3 text-sm">
          <div className="flex justify-between">
            <span className="text-text-secondary">Last Trade</span>
            <span className="font-mono text-xs">{health.last_trade?.split('T')[0] || 'never'}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Last Analysis</span>
            <span className="font-mono text-xs">{health.last_analysis || 'never'}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Last Evolution</span>
            <span className="font-mono text-xs">{health.last_evolution?.split('T')[0] || 'never'}</span>
          </div>
        </div>
      </Panel>
    </div>
  )
}
