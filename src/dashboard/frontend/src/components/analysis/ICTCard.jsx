import { useApi } from '../../hooks/useApi'
import Pill from '../ui/Pill'

// / fvgs and order blocks
export default function ICTCard({ symbol }) {
  const { data, loading } = useApi(`/api/ict-indicators/${symbol}`, 60000)

  if (loading && !data) return <div className="dim" style={{ padding: 14, fontSize: 12 }}>Loading...</div>
  if (!data || (!data.fvgs?.length && !data.order_blocks?.length && !data.structure_breaks?.length)) {
    return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No ICT data yet — computed next cycle</div>
  }

  const variantFor = t => t === 'bullish' ? 'bullish' : 'bearish'

  const Section = ({ label, children }) => (
    <div>
      <div style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 6 }}>{label}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>{children}</div>
    </div>
  )

  return (
    <div style={{ padding: '14px 16px', display: 'grid', gap: 12 }}>
      {data.fvgs?.length > 0 && (
        <Section label="fair value gaps">
          {data.fvgs.slice(0, 8).map((g, i) => (
            <Pill key={i} variant={variantFor(g.type)}>
              {g.type[0].toUpperCase()} ${parseFloat(g.low).toFixed(2)}–${parseFloat(g.high).toFixed(2)}
              {g.filled && <span className="dim"> ✓</span>}
            </Pill>
          ))}
        </Section>
      )}
      {data.order_blocks?.length > 0 && (
        <Section label="order blocks">
          {data.order_blocks.slice(0, 6).map((b, i) => (
            <Pill key={i} variant={variantFor(b.type)}>
              {b.type[0].toUpperCase()} ${parseFloat(b.low).toFixed(2)}–${parseFloat(b.high).toFixed(2)}
            </Pill>
          ))}
        </Section>
      )}
      {data.structure_breaks?.length > 0 && (
        <Section label="structure breaks">
          {data.structure_breaks.slice(0, 6).map((s, i) => (
            <Pill key={i} variant={variantFor(s.direction)}>
              {s.type.toUpperCase()} {s.direction[0].toUpperCase()} @${parseFloat(s.level).toFixed(2)}
            </Pill>
          ))}
        </Section>
      )}
    </div>
  )
}
