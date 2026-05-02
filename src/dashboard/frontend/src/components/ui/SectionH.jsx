// / v3 .sec-h section header

export default function SectionH({ num, title, em, meta, children, className = '' }) {
  return (
    <section className={`sec ${className}`.trim()}>
      <div className="sec-h">
        {num !== undefined && <span className="num">{num}</span>}
        <h2>
          {title}
          {em ? <> <span className="punct">.</span> <em>{em}</em></> : <span className="punct">.</span>}
        </h2>
        {meta && <span className="meta">{meta}</span>}
      </div>
      {children}
    </section>
  )
}
