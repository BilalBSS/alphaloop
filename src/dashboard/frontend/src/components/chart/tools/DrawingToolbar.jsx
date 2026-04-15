// / compact drawing toolbar — sits alongside IndicatorPicker in the price pane header
// / renders ascii glyph buttons for each tool; clicking activates the tool on the DrawingManager
// / "Clear" confirms via window.confirm before wiping all drawings for the current symbol
//
// / props:
// /   activeTool — string | null — current tool name from useDrawings
// /   setTool    — (toolName | null) => void — activate a tool; pass null for cursor
// /   clear      — () => Promise<void> — delete all drawings
// /   undo       — () => Promise<void> — remove the most recent drawing

// / tool list: glyph + label + library tool type string
// / trend-line, horizontal-line, vertical-line, rectangle, parallel-channel, fib-retracement, text-annotation
// / match the readonly type strings exposed by lightweight-charts-drawing
const TOOLS = [
  { id: null, glyph: '>', label: 'Cursor' },
  { id: 'trend-line', glyph: '/', label: 'Trendline' },
  { id: 'horizontal-line', glyph: 'H', label: 'HLine' },
  { id: 'vertical-line', glyph: 'V', label: 'VLine' },
  { id: 'rectangle', glyph: 'R', label: 'Rect' },
  { id: 'parallel-channel', glyph: 'P', label: 'Channel' },
  { id: 'fib-retracement', glyph: 'F', label: 'Fib' },
  { id: 'text-annotation', glyph: 'T', label: 'Text' },
]

export default function DrawingToolbar({ activeTool, setTool, clear, undo }) {
  const handleClick = (id) => {
    if (typeof setTool === 'function') setTool(id)
  }

  const handleClear = () => {
    if (typeof clear !== 'function') return
    if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
      if (!window.confirm('Delete all drawings for this symbol?')) return
    }
    clear()
  }

  const handleUndo = () => {
    if (typeof undo === 'function') undo()
  }

  return (
    <div className="flex items-center gap-1" role="toolbar" aria-label="Drawing tools">
      {TOOLS.map(tool => {
        const isActive = tool.id === activeTool || (tool.id === null && !activeTool)
        return (
          <button
            key={tool.label}
            type="button"
            onClick={() => handleClick(tool.id)}
            title={tool.label}
            aria-label={tool.label}
            aria-pressed={isActive}
            className={`px-1.5 py-0.5 text-[11px] font-mono font-semibold border transition-colors ${
              isActive
                ? 'border-accent text-accent bg-card'
                : 'border-border text-text-muted hover:text-text-primary bg-card'
            }`}
          >
            {tool.glyph}
          </button>
        )
      })}
      <button
        type="button"
        onClick={handleUndo}
        title="Undo"
        aria-label="Undo"
        className="px-1.5 py-0.5 text-[11px] font-mono font-semibold border border-border text-text-muted hover:text-text-primary bg-card"
      >
        U
      </button>
      <button
        type="button"
        onClick={handleClear}
        title="Clear all"
        aria-label="Clear all drawings"
        className="px-1.5 py-0.5 text-[11px] font-mono font-semibold border border-border text-loss hover:text-loss bg-card"
      >
        X
      </button>
    </div>
  )
}
