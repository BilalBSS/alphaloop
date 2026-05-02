import Card from '../ui/Card'
import { useApi } from '../../hooks/useApi'

// / staleness-derived feed status

function timeAgo(ts) {
  if (!ts) return 'never'
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 0) return 'just now'
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function statusFor(item) {
  if (item.status === 'red' || item.is_stale) return { dot: 'neg', label: 'stale', tone: 'neg' }
  if (item.status === 'yellow') return { dot: 'warn', label: 'lagging', tone: 'warn' }
  const s = parseFloat(item.staleness_hours)
  const t = parseFloat(item.threshold_hours)
  if (Number.isFinite(s) && Number.isFinite(t) && t > 0 && s / t > 0.6) {
    return { dot: 'warn', label: 'lagging', tone: 'warn' }
  }
  return { dot: '', label: 'ok', tone: 'pos' }
}

export default function DataFeedsPanel() {
  const { data, loading } = useApi('/api/staleness', 60000)
  const sources = Array.isArray(data) ? data : (data?.sources || [])

  return (
    <Card title={<><b>data feeds</b></>} meta="last tick" p0>
      {loading && sources.length === 0 ? (
        <div className="empty-state"><div className="empty-state-title">loading feeds</div></div>
      ) : sources.length === 0 ? (
        <div className="empty-state"><div className="empty-state-title">no feed data</div></div>
      ) : (
        sources.map((item, i) => {
          const status = statusFor(item)
          const errs = item.error_count_24h || 0
          return (
            <div key={`${item.source}-${i}`} className="svc">
              <span className="nm"><span className={`dot ${status.dot}`} />{item.source}</span>
              <span className="x">{timeAgo(item.last_update)}</span>
              <span className="x">{errs > 0 ? `${errs} err` : 'no errors'}</span>
              <span className={status.tone} style={{ fontSize: 11 }}>{status.label}</span>
            </div>
          )
        })
      )}
    </Card>
  )
}
