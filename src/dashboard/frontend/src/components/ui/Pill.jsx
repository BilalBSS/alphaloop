// / v3 .pill chip

const VARIANTS = new Set([
  'live', 'paper', 'killed',
  'long', 'short',
  'bull', 'bear', 'sideways', 'regime',
  'lesson', 'post', 'strat',
  'bullish', 'bearish', 'neutral',
  'up', 'warn-pill', 'new',
])

export default function Pill({ variant, children, className = '' }) {
  const v = VARIANTS.has(variant) ? variant : ''
  return <span className={`pill ${v} ${className}`.trim()}>{children}</span>
}
