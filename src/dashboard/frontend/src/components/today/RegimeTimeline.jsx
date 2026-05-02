import { useMemo } from 'react'
import Card from '../ui/Card'
import RegimeStrip from '../ui/RegimeTimeline'
import { useApi } from '../../hooks/useApi'

// / 90-day regime band

const NORMAL = { bull: 'bull', bear: 'bear', sideways: 'sideways', high_vol: 'highvol', highvol: 'highvol' }

export default function RegimeTimelineCard({ market = 'equity', days = 90 }) {
  const { data, loading } = useApi(`/api/regime-timeline?market=${market}&days=${days}`, 60000)

  const segments = useMemo(() => {
    const hist = Array.isArray(data?.history) ? data.history : []
    return hist.map((h, i) => ({
      key: h.date ?? i,
      regime: NORMAL[(h.regime || '').toLowerCase()] ?? '',
      title: `${h.date ?? ''} · ${h.regime ?? '—'}${h.confidence != null ? ` · p=${Number(h.confidence).toFixed(2)}` : ''}`,
    }))
  }, [data])

  const last = segments.length > 0 ? segments[segments.length - 1] : null
  const lastConf = (() => {
    const h = data?.history
    if (!Array.isArray(h) || h.length === 0) return null
    const c = h[h.length - 1]?.confidence
    return c != null ? Number(c).toFixed(2) : null
  })()

  return (
    <Card
      title={<><b>regime</b> · {days} sessions</>}
      meta="classifier output · daily"
    >
      {loading && segments.length === 0 ? (
        <div className="empty-state"><div className="empty-state-title">loading regime history</div></div>
      ) : segments.length === 0 ? (
        <div className="empty-state"><div className="empty-state-title">no regime history</div></div>
      ) : (
        <RegimeStrip
          segments={segments}
          axisLabels={[
            `− ${days}d`,
            `− ${Math.round(days * 2 / 3)}d`,
            `− ${Math.round(days / 3)}d`,
            last ? `today · ${last.regime || '—'}${lastConf ? ` ${lastConf}` : ''}` : 'today',
          ]}
        />
      )}
      <div className="legend" style={{ borderTop: 0, paddingLeft: 0, marginTop: 8 }}>
        <span><i style={{ background: 'rgba(127,184,122,.45)' }} /> bull</span>
        <span><i style={{ background: 'rgba(216,180,102,.45)' }} /> sideways</span>
        <span><i style={{ background: 'rgba(213,106,91,.45)' }} /> bear</span>
        <span><i style={{ background: 'rgba(199,154,58,.55)' }} /> high-vol</span>
      </div>
    </Card>
  )
}
