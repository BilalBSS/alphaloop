// / compact top chrome — brand + ws health indicator.
// / hero-level "what's happening now" now lives inside each tab to keep the header light.
export default function Header({ portfolio, wsStatus }) {
  const value = portfolio?.positions_count ?? '—'
  const statusColor = {
    connected: 'bg-profit',
    reconnecting: 'bg-warning',
    connecting: 'bg-warning',
    disconnected: 'bg-loss',
  }[wsStatus] || 'bg-text-muted'
  const statusLabel = wsStatus === 'connected' ? 'live'
    : wsStatus === 'reconnecting' || wsStatus === 'connecting' ? 'reconnecting'
    : 'offline'

  return (
    <header className="h-12 bg-bg-surface border-b border-border flex items-center px-6 gap-6 text-sm shrink-0">
      <span className="font-mono text-xl font-semibold text-text-primary tracking-wide">QTS</span>

      <div className="flex items-center gap-3 ml-auto">
        <span className="text-text-secondary text-xs">
          <span className="font-mono text-text-primary">{value}</span> positions
        </span>
        <div className="flex items-center gap-1.5" title={`WebSocket: ${wsStatus}`}>
          <div className={`w-2 h-2 rounded-full ${statusColor} ${wsStatus === 'connected' ? 'market-pulse' : ''}`} />
          <span className="text-[10px] uppercase text-text-muted tracking-wider">{statusLabel}</span>
        </div>
      </div>
    </header>
  )
}
