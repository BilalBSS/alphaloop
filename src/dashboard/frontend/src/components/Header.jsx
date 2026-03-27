export default function Header({ portfolio, wsStatus }) {
  const value = portfolio?.positions_count ?? '--'
  const statusColor = {
    connected: 'bg-profit',
    reconnecting: 'bg-warning',
    connecting: 'bg-warning',
    disconnected: 'bg-loss',
  }[wsStatus] || 'bg-text-muted'

  return (
    <header className="h-12 bg-bg-surface border-b border-border flex items-center px-4 gap-6 text-sm shrink-0">
      <span className="font-mono text-xl font-semibold text-text-primary">QTS</span>

      <div className="flex items-center gap-2 ml-auto">
        <span className="text-text-secondary text-xs">
          {value} positions
        </span>
        <div className={`w-2 h-2 rounded-full ${statusColor} ${wsStatus === 'connected' ? 'market-pulse' : ''}`}
          title={`WebSocket: ${wsStatus}`} />
      </div>
    </header>
  )
}
