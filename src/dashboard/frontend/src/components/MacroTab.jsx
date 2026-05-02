import { useMemo } from 'react'
import SectionH from './ui/SectionH'
import MacroTile from './macro/MacroTile'
import ClassifierInputs from './macro/ClassifierInputs'
import SizingMultipliers from './macro/SizingMultipliers'
import { useApi } from '../hooks/useApi'

// / fred tape series
const TAPE_SERIES = ['DGS10', 'DGS2', 'FEDFUNDS', 'CPIAUCSL', 'UNRATE', 'VIXCLS', 'DXY', 'WTI']

function regimeLabel(indicators, spread) {
  const ff = indicators.find((r) => r.series_id === 'FEDFUNDS')
  if (spread?.inverted) return 'recession risk'
  if (ff && parseFloat(ff.value) > 4.5) return 'tightening'
  if (ff && parseFloat(ff.value) < 2.0) return 'easing'
  return 'neutral'
}

function activeRegime(label) {
  if (label === 'recession risk' || label === 'tightening') return 'bear'
  if (label === 'easing') return 'bull'
  return 'sideways'
}

export default function MacroTab() {
  const { data: context, loading } = useApi('/api/macro-context', 60000)
  const { data: history } = useApi('/api/macro-history?days=30', 60000)

  const spread = context?.yield_curve_spread
  const latestBySeries = useMemo(() => {
    const m = {}
    for (const r of context?.indicators || []) m[r.series_id] = r
    return m
  }, [context])

  const indicators = context?.indicators || []
  const regime = regimeLabel(indicators, spread)
  const active = activeRegime(regime)

  return (
    <>
      <SectionH
        num="10"
        title="macro"
        em="tape"
        meta={<><code>/api/macro-context</code> · tape regime <span className="acc">{regime}</span></>}
      >
        {loading && indicators.length === 0 ? (
          <div className="empty-state"><div className="empty-state-title">loading FRED indicators</div></div>
        ) : (
          <div className="grid c4">
            {TAPE_SERIES.map((sid) => (
              <MacroTile
                key={sid}
                seriesId={sid}
                latest={latestBySeries[sid]}
                history={history?.series || {}}
              />
            ))}
          </div>
        )}
      </SectionH>

      <SectionH
        num="10b"
        title="macro regime"
        em="classifier + sizing"
        meta={<>sizing regime <span className="acc">{active}</span></>}
      >
        <div className="grid c2">
          <ClassifierInputs />
          <SizingMultipliers activeRegime={active} />
        </div>
      </SectionH>
    </>
  )
}
