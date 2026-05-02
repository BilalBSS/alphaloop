// / v3 .card wrapper

export default function Card({ title, meta, dense = false, p0 = false, className = '', children }) {
  const bodyClass = `card-b${p0 ? ' p0' : ''}${dense ? ' dense' : ''}`
  return (
    <div className={`card ${className}`.trim()}>
      {(title || meta) && (
        <div className="card-h">
          {title && <span className="t">{title}</span>}
          {meta && <span className="m">{meta}</span>}
        </div>
      )}
      <div className={bodyClass}>{children}</div>
    </div>
  )
}
