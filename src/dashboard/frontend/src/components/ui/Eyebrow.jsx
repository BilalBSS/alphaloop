// / uppercase mini-label

export default function Eyebrow({ children, className = '' }) {
  return (
    <span
      className={className}
      style={{
        fontSize: '10px',
        color: 'var(--ink-3)',
        letterSpacing: '0.16em',
        textTransform: 'uppercase',
        fontWeight: 500,
      }}
    >
      {children}
    </span>
  )
}
