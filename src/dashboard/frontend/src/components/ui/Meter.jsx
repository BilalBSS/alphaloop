// / v3 .meter

const TONES = new Set(['pos', 'neg', 'warn', 'acc-2'])

export default function Meter({ value, max = 1, tone, label, className = '' }) {
  const ratio = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0
  const fillTone = TONES.has(tone) ? tone : ''
  const display = label !== undefined ? label : ratio.toFixed(2)
  return (
    <div className={`meter ${className}`.trim()}>
      <div className="track">
        <div
          className={`fill ${fillTone}`.trim()}
          style={{ width: `${(ratio * 100).toFixed(1)}%` }}
        />
      </div>
      <span className="v">{display}</span>
    </div>
  )
}
