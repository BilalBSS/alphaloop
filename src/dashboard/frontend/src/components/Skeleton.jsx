// / content-shaped skeletons — approximate the final layout so loading doesn't jump.

export function SkeletonRow({ cols = 4 }) {
  return (
    <tr>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-3 py-2">
          <div className="skeleton h-4 w-full" />
        </td>
      ))}
    </tr>
  )
}

export function SkeletonChart() {
  return <div className="skeleton w-full h-48 rounded" />
}

export function SkeletonTable({ rows = 3, cols = 4 }) {
  return (
    <table className="w-full">
      <tbody>
        {Array.from({ length: rows }).map((_, i) => (
          <SkeletonRow key={i} cols={cols} />
        ))}
      </tbody>
    </table>
  )
}

// / strategy card skeleton — mirrors the layout of <StrategyCard/>
// / so the page doesn't reshuffle when real data arrives.
export function SkeletonStrategyCard() {
  return (
    <div className="bg-bg-primary border border-border rounded p-3">
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="skeleton h-4 w-3/4" />
          <div className="skeleton h-3 w-1/3" />
        </div>
        <div className="shrink-0 space-y-1">
          <div className="skeleton h-2 w-16" />
          <div className="skeleton h-6 w-14" />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 mb-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="space-y-1">
            <div className="skeleton h-2 w-12" />
            <div className="skeleton h-4 w-14" />
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between pt-2 border-t border-border">
        <div className="skeleton h-3 w-20" />
        <div className="skeleton h-4 w-10" />
      </div>
    </div>
  )
}

// / hero banner skeleton — three metrics side by side
export function SkeletonHero() {
  return (
    <div className="hero-banner">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="hero-metric space-y-1">
          <div className="skeleton h-2 w-16" />
          <div className="skeleton h-5 w-28" />
        </div>
      ))}
    </div>
  )
}

// / stacked lines skeleton — useful for text blocks (synthesis, summaries)
export function SkeletonLines({ lines = 3, widths = null }) {
  const defaultWidths = ['w-full', 'w-5/6', 'w-2/3', 'w-3/4', 'w-4/5']
  return (
    <div className="space-y-2">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className={`skeleton h-3 ${widths?.[i] || defaultWidths[i % defaultWidths.length]}`}
        />
      ))}
    </div>
  )
}
