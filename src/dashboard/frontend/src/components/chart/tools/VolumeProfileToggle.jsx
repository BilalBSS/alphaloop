// / compact toggle for the volume profile overlay
// / props: { enabled, setEnabled }
export default function VolumeProfileToggle({ enabled, setEnabled }) {
  const handleClick = () => {
    if (typeof setEnabled === 'function') setEnabled(!enabled)
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      title={enabled ? 'Hide volume profile' : 'Show volume profile'}
      aria-pressed={enabled}
      className={`px-2 py-0.5 text-[11px] uppercase font-semibold border transition-colors ${
        enabled
          ? 'border-accent text-accent bg-card'
          : 'border-border text-text-muted hover:text-text-primary bg-card'
      }`}
    >
      Vol Profile{enabled ? ' *' : ''}
    </button>
  )
}
