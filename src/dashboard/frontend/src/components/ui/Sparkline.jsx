// / inline svg sparkline

const TONE_VARS = {
  pos: 'var(--pos)',
  neg: 'var(--neg)',
  acc: 'var(--acc)',
  warn: 'var(--warn)',
  acc2: 'var(--acc-2)',
  ink: 'var(--ink-3)',
}

export default function Sparkline({
  data,
  width = 320,
  height = 42,
  tone = 'ink',
  strokeWidth = 1.25,
  fill = false,
  className = '',
}) {
  if (!data || data.length < 2) {
    return <svg width={width} height={height} className={className} aria-hidden />
  }

  const lo = Math.min(...data)
  const hi = Math.max(...data)
  const span = hi - lo || 1
  const step = data.length > 1 ? width / (data.length - 1) : width
  const points = data.map((v, i) => {
    const x = i * step
    const y = height - ((v - lo) / span) * height
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')

  const stroke = TONE_VARS[tone] || TONE_VARS.ink
  const polyfill = fill
    ? `M 0,${height} L ${points} L ${width},${height} Z`
    : null

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={className}
      aria-hidden
    >
      {polyfill && <path d={polyfill} fill={stroke} fillOpacity="0.15" />}
      <polyline
        points={points}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
