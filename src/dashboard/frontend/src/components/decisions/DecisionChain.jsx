// / 5-step decision chain

const TONES = new Set(['pos', 'neg'])

export default function DecisionChain({ steps }) {
  return (
    <div className="chain">
      {steps.map((s) => {
        const out = TONES.has(s.outTone) ? `out ${s.outTone}` : 'out'
        return (
          <div key={s.n} className="step">
            <div className="n">{s.n}</div>
            <div className="nm">{s.nm}</div>
            {s.d && <div className="d">{s.d}</div>}
            {s.out && <div className={out}>{s.out}</div>}
          </div>
        )
      })}
    </div>
  )
}
