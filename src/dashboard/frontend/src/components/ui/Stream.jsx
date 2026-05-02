// / v3 .stream events

const KINDS = new Set(['fill', 'signal', 'regime', 'risk', 'evolve', 'miss'])

export default function Stream({ events, emptyMessage = 'no recent activity' }) {
  if (!events || events.length === 0) {
    return <div className="empty-state"><div className="empty-state-title">{emptyMessage}</div></div>
  }
  return (
    <div className="stream">
      {events.map((e, i) => (
        <StreamRow key={e.id ?? i} ev={e} />
      ))}
    </div>
  )
}

function StreamRow({ ev }) {
  const kind = KINDS.has(ev.kind) ? ev.kind : 'signal'
  return (
    <div className="row">
      <span className="ts">{ev.ts}</span>
      <span className={`k ${kind}`}>{kind}</span>
      <span className="txt">{ev.text}</span>
      {ev.delta && <span className="delta">{ev.delta}</span>}
    </div>
  )
}
