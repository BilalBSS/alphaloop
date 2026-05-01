// / v3 .bar topbar

const fmtMoney = (n) => {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const sign = n < 0 ? '−' : ''
  const abs = Math.abs(Number(n))
  return `${sign}$${abs.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

const fmtCycle = (iso) => {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('en-US', { hour12: false, timeZone: 'America/New_York' }) + ' ET'
  } catch {
    return '—'
  }
}

export default function Header({ portfolio, health, macro, version, wsStatus, onRunCycle, onPauseExec, paused }) {
  const nav = portfolio?.equity ?? portfolio?.nav ?? portfolio?.total_value
  const pnl24 = portfolio?.pnl_24h ?? portfolio?.daily_pnl ?? portfolio?.pnl_today
  const positions = portfolio?.positions_count ?? portfolio?.positions?.length
  const positionsCap = portfolio?.max_open_positions ?? health?.risk?.max_open_positions
  const regime = macro?.regime ?? portfolio?.regime
  const regimeProb = macro?.regime_probability ?? macro?.p_bull
  const cycleTs = health?.next_cycle_ts ?? health?.next_cycle
  const versionLabel = version?.version ? `v${version.version}` : null
  const brokerMode = version?.broker_mode ?? portfolio?.broker_mode

  const wsLabel = wsStatus === 'connected' ? 'live'
    : (wsStatus === 'reconnecting' || wsStatus === 'connecting') ? 'reconnecting'
    : 'offline'
  const wsTone = wsStatus === 'connected' ? 'pos'
    : (wsStatus === 'reconnecting' || wsStatus === 'connecting') ? 'warn'
    : 'neg'

  const pnlClass = pnl24 === undefined || pnl24 === null ? '' : (pnl24 < 0 ? 'neg' : 'pos')

  return (
    <header className="bar">
      <div className="b-l">
        <span className="logo">▮ alphaloop</span>
        {(versionLabel || brokerMode) && (
          <span className="v">
            {versionLabel}{versionLabel && brokerMode ? ' · ' : ''}{brokerMode}
          </span>
        )}
      </div>

      <div className="b-c">
        <span><b>NAV</b> {fmtMoney(nav)}</span>
        <span>
          <b>P/L 24h</b>{' '}
          <span className={pnlClass}>{fmtMoney(pnl24)}</span>
        </span>
        <span>
          <b>regime</b> {regime ?? '—'}
          {regimeProb !== undefined && regimeProb !== null
            ? ` · ${Number(regimeProb).toFixed(2)}`
            : ''}
        </span>
        <span><b>positions</b> {positions ?? '—'}{positionsCap ? `/${positionsCap}` : ''}</span>
        <span><b>cycle</b> {fmtCycle(cycleTs)}</span>
      </div>

      <div className="b-r">
        <button
          type="button"
          className="btn"
          onClick={onRunCycle}
          title="trigger /api/admin/trigger/strategy"
        >
          ⟳ run cycle
        </button>
        <button
          type="button"
          className={`btn ${paused ? 'neg' : 'warn'}`.trim()}
          onClick={onPauseExec}
          title="trigger /api/admin/pause"
        >
          {paused ? '▶ resume exec' : '‖ pause exec'}
        </button>
        <span title={`websocket: ${wsStatus}`} style={{ display: 'inline-flex', alignItems: 'center' }}>
          <span className={`pulse ${wsTone === 'pos' ? '' : wsTone}`.trim()} />
          WS · {wsLabel}
        </span>
      </div>
    </header>
  )
}

