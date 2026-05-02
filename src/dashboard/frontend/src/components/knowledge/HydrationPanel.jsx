import Card from '../ui/Card'
import { useApi } from '../../hooks/useApi'

// / wiki hydration progress

function timeAgo(ts) {
  if (!ts) return 'never'
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 0) return 'just now'
  const m = Math.floor(diff / 60000)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function timeUntil(ts) {
  if (!ts) return '—'
  const diff = new Date(ts).getTime() - Date.now()
  if (diff < 0) return 'due'
  const h = Math.floor(diff / 3600000)
  const m = Math.floor((diff % 3600000) / 60000)
  if (h < 24) return `${h}h ${m}m`
  return `${Math.floor(h / 24)}d`
}

export default function HydrationPanel() {
  const { data } = useApi('/api/hydration-status', 60000)
  if (!data) return null
  const { daily_cap = 5, hydrated_today = 0, next_fire_ts, last_event_ts, last_status } = data
  const pct = daily_cap > 0 ? Math.min(100, (hydrated_today / daily_cap) * 100) : 0
  const tone = pct >= 100 ? 'pos' : pct > 0 ? 'acc' : 'dim'
  const statusTone = last_status === 'ok' ? 'pos' : last_status === 'error' ? 'neg' : 'dim'

  return (
    <Card title={<><b>symbol wiki hydration</b></>} meta={`next run ${timeUntil(next_fire_ts)}`}>
      <div style={{ marginBottom: 10 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 14 }}>
            {hydrated_today} <span className="dim" style={{ fontSize: 11 }}>/ {daily_cap} hydrated today</span>
          </span>
          <span className="dim" style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase' }}>
            cap WIKI_HYDRATION_DAILY_CAP
          </span>
        </div>
        <div style={{ width: '100%', background: 'var(--bg-3)', height: 4 }}>
          <div style={{ height: '100%', width: `${pct}%`, background: `var(--${tone === 'dim' ? 'line' : tone})` }} />
        </div>
      </div>
      <div className="dim" style={{ fontSize: 11 }}>
        last fire {timeAgo(last_event_ts)} · status <span className={statusTone}>{last_status || 'pending'}</span>
      </div>
    </Card>
  )
}
