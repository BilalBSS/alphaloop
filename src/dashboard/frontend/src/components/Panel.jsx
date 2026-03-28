import { useState } from 'react'

export default function Panel({ title, children, className = '', error = null, collapsible = false, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className={`bg-bg-surface border border-border p-4 ${className}`}>
      {title && (
        <h3
          className={`text-[11px] uppercase tracking-wider text-text-secondary font-semibold flex items-center justify-between ${collapsible ? 'cursor-pointer select-none' : ''} ${open || !collapsible ? 'mb-3' : ''}`}
          onClick={collapsible ? () => setOpen(o => !o) : undefined}
        >
          {title}
          {collapsible && (
            <span className={`text-text-muted text-[10px] transition-transform duration-150 ${open ? 'rotate-90' : ''}`}>
              &#9654;
            </span>
          )}
        </h3>
      )}
      {(!collapsible || open) && (
        error ? (
          <div className="text-loss text-sm border-l-2 border-loss pl-3 py-2">
            {error}
          </div>
        ) : children
      )}
    </div>
  )
}
