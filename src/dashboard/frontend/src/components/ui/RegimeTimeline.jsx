// / 90-day regime band strip

const REGIMES = new Set(['bull', 'bear', 'sideways', 'highvol'])

export default function RegimeTimeline({ segments, axisLabels }) {
  return (
    <div>
      <div className="regtl">
        {segments.map((seg, i) => {
          const cls = REGIMES.has(seg.regime) ? seg.regime : ''
          return <div key={seg.key ?? i} className={`seg ${cls}`.trim()} title={seg.title} />
        })}
      </div>
      {axisLabels && (
        <div className="regtl-ax">
          {axisLabels.map((l, i) => <span key={i}>{l}</span>)}
        </div>
      )}
    </div>
  )
}
