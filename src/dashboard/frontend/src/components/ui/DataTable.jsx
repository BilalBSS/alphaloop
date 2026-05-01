// / v3 .tbl

export default function DataTable({
  columns,
  rows,
  keyField = 'id',
  onRowClick,
  selectedKey,
  emptyMessage = 'no rows',
  className = '',
}) {
  if (!rows || rows.length === 0) {
    return <div className="empty-state"><div className="empty-state-title">{emptyMessage}</div></div>
  }
  return (
    <table className={`tbl ${className}`.trim()}>
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.key} className={c.align === 'right' ? 'r' : ''}>{c.label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const k = row[keyField]
          const sel = selectedKey !== undefined && k === selectedKey
          const click = onRowClick ? () => onRowClick(row) : undefined
          return (
            <tr
              key={k}
              className={[sel ? 'sel' : '', click ? 'click' : ''].join(' ').trim()}
              onClick={click}
            >
              {columns.map((c) => {
                const cellVal = c.render ? c.render(row) : row[c.key]
                const cls = [
                  c.align === 'right' ? 'r' : '',
                  c.cellClass ? c.cellClass(row) : '',
                ].join(' ').trim()
                return <td key={c.key} className={cls}>{cellVal}</td>
              })}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
