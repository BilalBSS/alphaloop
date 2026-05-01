// / v3 .gauges cell

const TONES = new Set(['pos', 'warn', 'neg'])

export default function Gauge({ label, value, valueTone, fillRatio, fillTone, limit }) {
  const vt = TONES.has(valueTone) ? valueTone : ''
  const ft = TONES.has(fillTone) ? fillTone : ''
  const ratio = Math.max(0, Math.min(1, fillRatio ?? 0))
  return (
    <div className="g">
      <div className="lab">{label}</div>
      <div className={`v ${vt}`.trim()}>{value}</div>
      {limit && <div className="lim">{limit}</div>}
      {fillRatio !== undefined && (
        <div className="bar">
          <i className={ft} style={{ width: `${(ratio * 100).toFixed(1)}%` }} />
        </div>
      )}
    </div>
  )
}

export function GaugeRow({ children }) {
  return <div className="gauges">{children}</div>
}
