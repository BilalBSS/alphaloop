import { useEffect, useState } from 'react'

// / compact compare ui — input for the against symbol + toggle button that flips compare on/off
// / props: { base, against, setAgainst, enabled, setEnabled }
// / local draft state keeps user typing fluid; commit on enter / blur / click-toggle
export default function CompareBar({ base, against, setAgainst, enabled, setEnabled }) {
  const [draft, setDraft] = useState(against || '')

  // / sync draft when parent changes the against symbol (e.g. on symbol switch)
  useEffect(() => {
    setDraft(against || '')
  }, [against])

  const commit = () => {
    const next = draft.trim().toUpperCase()
    if (next !== against) setAgainst(next)
  }

  const handleKey = (e) => {
    if (e.key === 'Enter') {
      commit()
      if (!enabled && draft.trim()) setEnabled(true)
    } else if (e.key === 'Escape') {
      setDraft(against || '')
    }
  }

  const handleToggle = () => {
    commit()
    if (typeof setEnabled === 'function') setEnabled(!enabled)
  }

  return (
    <div className="flex items-center gap-1" role="group" aria-label="Compare controls">
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={handleKey}
        placeholder="vs"
        aria-label="Compare against symbol"
        className="w-16 px-1.5 py-0.5 text-[11px] font-mono uppercase bg-card border border-border text-text-primary focus:outline-none focus:border-accent"
        maxLength={20}
        disabled={!base}
      />
      <button
        type="button"
        onClick={handleToggle}
        disabled={!base || !draft.trim()}
        title={enabled ? 'Hide compare overlay' : 'Show compare overlay'}
        aria-pressed={enabled}
        className={`px-2 py-0.5 text-[11px] uppercase font-semibold border transition-colors ${
          enabled
            ? 'border-accent text-accent bg-card'
            : 'border-border text-text-muted hover:text-text-primary bg-card disabled:opacity-40 disabled:cursor-not-allowed'
        }`}
      >
        Compare{enabled ? ' *' : ''}
      </button>
    </div>
  )
}
