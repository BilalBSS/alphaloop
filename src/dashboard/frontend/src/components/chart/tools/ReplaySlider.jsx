import { useMemo } from 'react'

// / compact replay slider — observation-mode only, zero re-simulation
// / props:
// /   enabled         — boolean          — whether replay mode is active
// /   onToggle        — () => void       — flips enabled in parent
// /   onCutoffChange  — (iso) => void    — called with the new iso cutoff as user drags
// /   minT            — iso string|null  — window start (from snapshot or bars)
// /   maxT            — iso string|null  — window end   (from snapshot or bars)
// /   cutoff          — iso string|null  — current cutoff position
// /
// / the slider maps [0..100] percentage to the [minT..maxT] time window
// / when minT/maxT are not known yet we fall back to "now" so the button still works
// / pct is derived from props (not state) so parent is the single source of truth — no cascading renders

function _toMs(iso) {
  if (!iso) return null
  const t = Date.parse(iso)
  return Number.isFinite(t) ? t : null
}

function _fmtCutoff(iso) {
  if (!iso) return '--'
  const ms = _toMs(iso)
  if (ms == null) return iso
  const d = new Date(ms)
  // / compact: yyyy-mm-dd hh:mm utc so the user sees precisely the replay cutoff
  const y = d.getUTCFullYear()
  const mo = String(d.getUTCMonth() + 1).padStart(2, '0')
  const da = String(d.getUTCDate()).padStart(2, '0')
  const h = String(d.getUTCHours()).padStart(2, '0')
  const mi = String(d.getUTCMinutes()).padStart(2, '0')
  return `${y}-${mo}-${da} ${h}:${mi}Z`
}

export default function ReplaySlider({
  enabled,
  onToggle,
  onCutoffChange,
  minT,
  maxT,
  cutoff,
}) {
  const minMs = useMemo(() => _toMs(minT), [minT])
  const maxMs = useMemo(() => _toMs(maxT), [maxT])
  const cutoffMs = useMemo(() => _toMs(cutoff), [cutoff])

  // / derive slider percentage purely from props so parent stays the single source of truth
  const pct = useMemo(() => {
    if (minMs == null || maxMs == null || cutoffMs == null) return 100
    if (maxMs <= minMs) return 100
    const p = ((cutoffMs - minMs) / (maxMs - minMs)) * 100
    return Math.max(0, Math.min(100, p))
  }, [minMs, maxMs, cutoffMs])

  const handleToggle = () => {
    if (typeof onToggle === 'function') onToggle()
  }

  const handleRange = (e) => {
    const next = Number(e.target.value)
    if (typeof onCutoffChange !== 'function') return
    if (minMs == null || maxMs == null || maxMs <= minMs) {
      // / no window yet — emit now so backend uses its own fallback
      onCutoffChange(new Date().toISOString())
      return
    }
    const ms = minMs + ((maxMs - minMs) * next) / 100
    onCutoffChange(new Date(ms).toISOString())
  }

  return (
    <div className="flex items-center gap-2" role="group" aria-label="Replay controls">
      <button
        type="button"
        onClick={handleToggle}
        title={enabled ? 'Exit replay mode' : 'Enter replay mode'}
        aria-pressed={enabled}
        className={`px-2 py-0.5 text-[11px] uppercase font-semibold border transition-colors ${
          enabled
            ? 'border-loss text-loss bg-card'
            : 'border-border text-text-muted hover:text-text-primary bg-card'
        }`}
      >
        Replay{enabled ? ' *' : ''}
      </button>
      {enabled && (
        <>
          <input
            type="range"
            min="0"
            max="100"
            step="0.5"
            value={pct}
            onChange={handleRange}
            aria-label="Replay cutoff position"
            className="w-32 accent-loss"
          />
          <span className="text-[10px] font-mono text-loss tabular-nums whitespace-nowrap">
            {_fmtCutoff(cutoff)}
          </span>
        </>
      )}
    </div>
  )
}
