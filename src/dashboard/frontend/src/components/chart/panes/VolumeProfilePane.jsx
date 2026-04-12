import { useMemo } from 'react'

// / horizontal volume-at-price histogram pinned to the right edge of the chart container
// / renders as absolutely-positioned divs stacked top-down (highest price first) so the vertical
// / axis visually aligns with the price scale — width proportional to bin volume / max bin volume
// / poc bucket highlighted in a brighter accent color
// / props: { profile, visible }
// / profile shape: { bins: [{price_low, price_high, volume, pct}], poc, vah, val, total_volume }
// / when visible is false or profile missing: returns null so the overlay unmounts cleanly
const MAX_WIDTH_PCT = 30
const POC_COLOR = 'rgba(245, 166, 35, 0.75)'
const VA_COLOR = 'rgba(120, 180, 255, 0.45)'
const BIN_COLOR = 'rgba(120, 180, 255, 0.2)'

function _fmtPrice(v) {
  if (v == null || !Number.isFinite(v)) return '--'
  if (Math.abs(v) >= 1000) return v.toFixed(0)
  if (Math.abs(v) >= 10) return v.toFixed(1)
  return v.toFixed(2)
}

export default function VolumeProfilePane({ profile, visible }) {
  const derived = useMemo(() => {
    if (!visible || !profile || !Array.isArray(profile.bins) || profile.bins.length === 0) {
      return null
    }
    const maxVol = profile.bins.reduce((acc, b) => Math.max(acc, parseFloat(b.volume) || 0), 0)
    if (maxVol <= 0) return null
    const poc = profile.poc
    const vah = parseFloat(profile.vah)
    const val = parseFloat(profile.val)
    // / top-down render order: highest price first
    const ordered = [...profile.bins].sort((a, b) => parseFloat(b.price_high) - parseFloat(a.price_high))
    return { ordered, maxVol, poc, vah, val }
  }, [profile, visible])

  if (!derived) return null

  const { ordered, maxVol, poc, vah, val } = derived

  return (
    <div
      className="absolute top-0 right-0 bottom-0 pointer-events-none flex flex-col justify-stretch z-10"
      style={{ width: `${MAX_WIDTH_PCT}%` }}
    >
      {ordered.map((bin, idx) => {
        const vol = parseFloat(bin.volume) || 0
        const barWidth = vol > 0 ? (vol / maxVol) * 100 : 0
        const priceMid = (parseFloat(bin.price_low) + parseFloat(bin.price_high)) / 2
        const isPoc = poc && Math.abs(priceMid - (parseFloat(poc.price_low) + parseFloat(poc.price_high)) / 2) < 1e-9
        const inValueArea = Number.isFinite(vah) && Number.isFinite(val)
          && parseFloat(bin.price_high) <= vah + 1e-9
          && parseFloat(bin.price_low) >= val - 1e-9
        const bg = isPoc ? POC_COLOR : inValueArea ? VA_COLOR : BIN_COLOR
        return (
          <div
            key={idx}
            className="flex-1 flex items-center justify-end relative"
            title={`${_fmtPrice(parseFloat(bin.price_low))} - ${_fmtPrice(parseFloat(bin.price_high))} · vol ${vol.toFixed(0)}`}
          >
            <div
              className="h-full"
              style={{ width: `${barWidth}%`, background: bg }}
            />
            {isPoc && (
              <span className="absolute right-1 text-[9px] font-mono text-accent font-semibold">POC</span>
            )}
          </div>
        )
      })}
    </div>
  )
}
