import Card from '../ui/Card'
import { useApi } from '../../hooks/useApi'

// / regime → size scalar table

const REGIME_ORDER = ['bull', 'sideways', 'bear', 'high_vol', 'insufficient_data']

function rowTone(r, isActive) {
  if (isActive) return 'pos'
  if (r === 'bear' || r === 'high_vol') return 'neg'
  return ''
}

export default function SizingMultipliers({ activeRegime }) {
  const { data } = useApi('/api/risk/sizing-multipliers', 300000)
  const m = data?.multipliers || {}
  const regimes = REGIME_ORDER.filter((r) => r in m)

  return (
    <Card
      title={<><b>sizing multipliers</b> · by regime</>}
      meta={<><code>configs/risk_limits.json</code></>}
      p0
    >
      {regimes.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">no multipliers loaded</div>
        </div>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>regime</th>
              <th className="r">multiplier</th>
            </tr>
          </thead>
          <tbody>
            {regimes.map((r) => {
              const isActive = activeRegime && r === activeRegime
              const tone = rowTone(r, isActive)
              return (
                <tr key={r}>
                  <td className={tone}>
                    {isActive ? <b>{r} <span className="dim">(active)</span></b> : r}
                  </td>
                  <td className={`r ${tone}`}>{Number(m[r]).toFixed(2)}×</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Card>
  )
}
