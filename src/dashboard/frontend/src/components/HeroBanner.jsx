// / hero banner — tab-level "what's happening right now" strip.
// / pure presentational: children arrange themselves via the `.hero-metric` helpers
// / defined in design-tokens.css. wrap whatever makes sense per tab.
export default function HeroBanner({ children, className = '' }) {
  return (
    <div className={`hero-banner ${className}`}>
      {children}
    </div>
  )
}
