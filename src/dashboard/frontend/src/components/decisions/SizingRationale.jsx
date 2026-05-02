// / why this size

import KVList from '../ui/KVList.jsx'

export default function SizingRationale({ details }) {
  if (!details) return null
  const entries = []
  if (details.strength != null) entries.push({ k: 'signal strength', v: Number(details.strength).toFixed(3) })
  if (details.kelly_fraction != null) entries.push({ k: 'kelly fraction', v: Number(details.kelly_fraction).toFixed(3) })
  if (details.regime) entries.push({ k: 'regime', v: details.regime })
  if (details.regime_multiplier != null) entries.push({ k: 'regime ×', v: `×${Number(details.regime_multiplier).toFixed(2)}` })
  if (details.side) entries.push({ k: 'side', v: details.side })
  if (details.final_qty != null) entries.push({ k: 'final qty', v: details.final_qty })
  if (entries.length === 0) return null
  return <KVList entries={entries} />
}
