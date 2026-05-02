// / 8-cell gate grid

const STATUS = new Set(['pass', 'warn', 'fail'])

export default function GateGrid({ gates }) {
  return (
    <div className="gates">
      {gates.map((g) => {
        const s = STATUS.has(g.status) ? g.status : ''
        return (
          <div key={g.name} className={`g ${s}`.trim()}>
            <span className="nm">{g.name}</span>
            <span className="v">{g.value ?? '—'}{g.limit !== null && g.limit !== undefined ? ` / ${g.limit}` : ''}</span>
          </div>
        )
      })}
    </div>
  )
}
