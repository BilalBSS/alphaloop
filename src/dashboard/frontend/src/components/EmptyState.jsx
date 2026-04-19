// / informative empty state — replaces literal "--" and "no data" placeholders.
// / title = one-line explanation; hint = why it's empty + when it'll populate.
export default function EmptyState({ title, hint, icon = null }) {
  return (
    <div className="empty-state">
      {icon && <div className="text-text-muted">{icon}</div>}
      <div className="empty-state-title">{title}</div>
      {hint && <div className="empty-state-hint">{hint}</div>}
    </div>
  )
}
