export default function Panel({ title, children, className = '', error = null }) {
  return (
    <div className={`bg-bg-surface border border-border p-4 ${className}`}>
      {title && (
        <h3 className="text-[11px] uppercase tracking-wider text-text-secondary mb-3 font-semibold">
          {title}
        </h3>
      )}
      {error ? (
        <div className="text-loss text-sm border-l-2 border-loss pl-3 py-2">
          {error}
        </div>
      ) : children}
    </div>
  )
}
