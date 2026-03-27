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
