export function renderInline(text, keyPrefix = '') {
  const parts = []
  let i = 0
  let buf = ''
  let n = 0
  const push = (node) => {
    if (buf) { parts.push(buf); buf = '' }
    parts.push(node)
  }
  while (i < text.length) {
    if (text.startsWith('**', i)) {
      const end = text.indexOf('**', i + 2)
      if (end > i) {
        push(<b key={`${keyPrefix}b${n++}`}>{text.slice(i + 2, end)}</b>)
        i = end + 2
        continue
      }
    }
    if (text[i] === '`') {
      const end = text.indexOf('`', i + 1)
      if (end > i) {
        push(<code key={`${keyPrefix}c${n++}`} className="ic">{text.slice(i + 1, end)}</code>)
        i = end + 1
        continue
      }
    }
    buf += text[i]
    i++
  }
  if (buf) parts.push(buf)
  return parts
}

export function renderMarkdown(src) {
  if (!src) return null
  const lines = src.split('\n')
  const blocks = []
  let bullets = null
  let i = 0
  const flush = () => {
    if (bullets) { blocks.push({ kind: 'ul', items: bullets }); bullets = null }
  }
  while (i < lines.length) {
    const raw = lines[i]
    const line = raw.replace(/\s+$/, '')
    if (!line.trim()) { flush(); i++; continue }
    const h = /^(#{1,6})\s+(.*)$/.exec(line)
    if (h) {
      flush()
      blocks.push({ kind: 'h', level: h[1].length, text: h[2] })
      i++; continue
    }
    const bul = /^[\-\*]\s+(.*)$/.exec(line)
    if (bul) {
      bullets = bullets || []
      bullets.push(bul[1])
      i++; continue
    }
    flush()
    blocks.push({ kind: 'p', text: line.trim() })
    i++
  }
  flush()
  return blocks.map((b, k) => {
    if (b.kind === 'h') {
      const sz = b.level === 1 ? 18 : b.level === 2 ? 15 : 13
      return (
        <div
          key={k}
          style={{
            fontSize: sz, fontWeight: 600, color: 'var(--ink)',
            margin: k === 0 ? '0 0 8px' : '14px 0 6px',
            letterSpacing: b.level <= 2 ? '-0.01em' : '0',
          }}
        >
          {renderInline(b.text, `${k}-`)}
        </div>
      )
    }
    if (b.kind === 'ul') {
      return (
        <ul key={k} style={{ margin: '4px 0 8px 18px', padding: 0, color: 'var(--ink)', fontSize: 13, lineHeight: 1.6 }}>
          {b.items.map((it, j) => <li key={j}>{renderInline(it, `${k}-${j}-`)}</li>)}
        </ul>
      )
    }
    return (
      <p key={k} style={{ margin: '0 0 8px', color: 'var(--ink)', fontSize: 13, lineHeight: 1.65 }}>
        {renderInline(b.text, `${k}-`)}
      </p>
    )
  })
}
