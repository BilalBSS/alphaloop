import Card from '../ui/Card'
import { useApi } from '../../hooks/useApi'

// / heartbeat-derived process status

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

// / freshness → status
function freshness(ts, freshMins, staleMins) {
  if (!ts) return { dot: 'neg', label: 'never', tone: 'neg' }
  const ageMin = (Date.now() - new Date(ts).getTime()) / 60000
  if (ageMin <= freshMins) return { dot: '', label: 'up', tone: 'pos' }
  if (ageMin <= staleMins) return { dot: 'warn', label: 'idle', tone: 'warn' }
  return { dot: 'neg', label: 'stale', tone: 'neg' }
}

function Row({ name, x1, x2, status }) {
  return (
    <div className="svc">
      <span className="nm"><span className={`dot ${status.dot}`} />{name}</span>
      <span className="x">{x1}</span>
      <span className="x">{x2}</span>
      <span className={status.tone} style={{ fontSize: 11 }}>{status.label}</span>
    </div>
  )
}

export default function ProcessesPanel({ health }) {
  const { data: loopsData } = useApi('/api/loops', 30000)
  const cycles = health?.cycles || {}
  const dbOk = health?.db_connected
  const storageMb = health?.storage?.db_size_mb
  const conns = health?.connections || {}

  const evolution = (loopsData?.loops || []).find((l) => l.name === 'evolution')

  const rows = [
    {
      name: 'orchestrator',
      x1: timeAgo(cycles.last_analysis),
      x2: `${cycles.symbols_today ?? 0} sym today`,
      status: freshness(cycles.last_analysis, 120, 360),
    },
    {
      name: 'strategy_eval',
      x1: timeAgo(cycles.last_strategy_eval),
      x2: 'every 15m',
      status: freshness(cycles.last_strategy_eval, 30, 120),
    },
    {
      name: 'executor',
      x1: timeAgo(cycles.last_trade),
      x2: 'event-driven',
      status: cycles.last_trade ? freshness(cycles.last_trade, 60 * 24, 60 * 24 * 3) : { dot: '', label: 'idle', tone: 'dim' },
    },
    {
      name: 'evolution_cron',
      x1: evolution?.last_fire_ts ? timeAgo(evolution.last_fire_ts) : 'never',
      x2: 'cron 02:30 ET',
      status: evolution?.last_status === 'error' ? { dot: 'neg', label: 'error', tone: 'neg' } :
              evolution?.last_status === 'ok' ? { dot: '', label: 'ok', tone: 'pos' } :
              { dot: 'warn', label: 'idle', tone: 'warn' },
    },
    {
      name: 'postgres',
      x1: storageMb != null ? `${storageMb} MB` : '—',
      x2: `conn ${conns.active ?? '—'}`,
      status: dbOk ? { dot: '', label: 'up', tone: 'pos' } : { dot: 'neg', label: 'down', tone: 'neg' },
    },
  ]

  return (
    <Card title={<><b>processes</b></>} meta="heartbeat" p0>
      {rows.map((r) => <Row key={r.name} {...r} />)}
    </Card>
  )
}
