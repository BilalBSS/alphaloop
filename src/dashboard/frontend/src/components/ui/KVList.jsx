// / v3 .kv list

export default function KVList({ entries, className = '' }) {
  return (
    <dl className={`kv ${className}`.trim()}>
      {entries.map((e, i) => (
        <KVRow key={e.k ?? i} k={e.k} v={e.v} />
      ))}
    </dl>
  )
}

function KVRow({ k, v }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </>
  )
}
