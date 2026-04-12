import { useEffect, useMemo, useRef, useState } from 'react'
import { INDICATORS } from '../indicatorRegistry'

// / picker popover: toggles indicators on the chart, persisted via useChartState
// / groups map user-facing categories to indicator ids (order within a group is display order)
// / keyboard: Escape closes. Arrow-key nav not wired in v1 — mouse/click only.
const GROUPS = [
  {
    label: 'Price Overlays',
    ids: ['sma_20', 'sma_50', 'sma_200', 'ema_20', 'ema_50', 'ema_200', 'fib_auto_100'],
  },
  {
    label: 'Volatility',
    ids: ['bb_20_2', 'keltner_20_10_2', 'donchian_20', 'supertrend_10_3', 'vwap', 'psar_2_20', 'ichimoku_9_26_52_26', 'atr_14'],
  },
  {
    label: 'Momentum',
    ids: ['rsi_14', 'macd_12_26_9', 'stoch_14_3_3', 'adx_14', 'cci_20', 'williams_14', 'roc_12'],
  },
  {
    label: 'Volume',
    ids: ['obv', 'mfi_14'],
  },
]

function labelFor(id) {
  const spec = INDICATORS[id]
  return (spec && spec.label) || id
}

export default function IndicatorPicker({ selected = [], onToggle }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)

  // / outside-click + escape close
  useEffect(() => {
    if (!open) return undefined
    const onDocClick = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const selectedSet = useMemo(() => new Set(Array.isArray(selected) ? selected : []), [selected])
  const count = selectedSet.size

  const handleToggle = (id) => {
    if (typeof onToggle === 'function') onToggle(id)
  }

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className={`px-2 py-0.5 text-[11px] uppercase font-semibold border transition-colors ${
          open || count > 0
            ? 'border-accent text-accent'
            : 'border-border text-text-muted hover:text-text-primary'
        }`}
        aria-expanded={open}
        aria-haspopup="true"
      >
        Indicators{count > 0 ? ` (${count})` : ''}
        <span className="ml-1 text-[9px]">{open ? 'v' : '>'}</span>
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-20 w-64 max-h-96 overflow-y-auto bg-card border border-border shadow-lg"
          role="menu"
        >
          {GROUPS.map(group => (
            <div key={group.label} className="border-b border-border last:border-b-0">
              <div className="px-2 py-1 text-[10px] uppercase text-text-secondary bg-bg-primary/50">
                {group.label}
              </div>
              <div>
                {group.ids.map(id => {
                  if (!INDICATORS[id]) return null
                  const checked = selectedSet.has(id)
                  return (
                    <label
                      key={id}
                      className="flex items-center gap-2 px-2 py-1 text-xs cursor-pointer hover:bg-bg-primary/60"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => handleToggle(id)}
                        className="accent-accent"
                      />
                      <span className={checked ? 'text-text-primary' : 'text-text-muted'}>
                        {labelFor(id)}
                      </span>
                    </label>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
