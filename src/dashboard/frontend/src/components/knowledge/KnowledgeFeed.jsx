import { useState, useMemo, useEffect } from 'react'
import Card from '../ui/Card'
import Pill from '../ui/Pill'
import { useApi } from '../../hooks/useApi'

// / unified .know feed

const TYPES = ['all', 'regime', 'lesson', 'post', 'strat']

function tsOf(item) {
  const t = item.updated_at || item.created_at || item.detected_at || item.modified_at
  return t ? new Date(t).getTime() : 0
}

function categoryToType(cat) {
  const c = (cat || '').toLowerCase()
  if (c.includes('regime')) return 'regime'
  if (c.includes('post')) return 'post'
  if (c.includes('lesson')) return 'lesson'
  if (c.includes('strat') || c.includes('symbol')) return 'strat'
  return 'lesson'
}

function fmtWhen(ts) {
  if (!ts) return '—'
  const diff = Date.now() - ts
  const m = Math.floor(diff / 60000)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function WikiBody({ path }) {
  return <WikiBodyInner key={path} path={path} />
}

function renderInline(text, keyPrefix = '') {
  // / bold + inline code + italic
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

function renderMarkdown(src) {
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

function WikiBodyInner({ path }) {
  const [state, setState] = useState({ content: null, error: null })
  useEffect(() => {
    let cancelled = false
    fetch(`/api/wiki/document?path=${encodeURIComponent(path)}`)
      .then((r) => r.ok ? r.json() : Promise.reject(`${r.status}`))
      .then((j) => { if (!cancelled) setState({ content: j.content, error: null }) })
      .catch((e) => { if (!cancelled) setState({ content: null, error: String(e) }) })
    return () => { cancelled = true }
  }, [path])
  if (state.error) return <div className="neg" style={{ fontSize: 12 }}>error: {state.error}</div>
  if (state.content === null) return <div className="dim" style={{ fontSize: 12 }}>loading…</div>
  return <>{renderMarkdown(state.content)}</>
}

function ExpandedDetail({ item }) {
  const t = item._type
  if (t === 'post') {
    return (
      <div style={{ padding: '12px 16px', background: 'var(--bg-3)', fontSize: 11, borderTop: '1px solid var(--line)' }}>
        <div className="dim" style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 6 }}>
          {item.symbol || '—'} · {item.trigger_type || '—'} · pnl {item.pnl != null ? `$${Number(item.pnl).toFixed(2)}` : '—'}
        </div>
        {item.details && (
          <pre style={{ margin: 0, fontSize: 11, whiteSpace: 'pre-wrap', maxHeight: 300, overflowY: 'auto', color: 'var(--ink-2)' }}>
            {typeof item.details === 'string' ? item.details : JSON.stringify(item.details, null, 2)}
          </pre>
        )}
      </div>
    )
  }
  if (t === 'regime') {
    return (
      <div style={{ padding: '12px 16px', background: 'var(--bg-3)', fontSize: 11, borderTop: '1px solid var(--line)' }}>
        <div>
          <span className="dim">{item.market || '—'}</span>{' · '}
          <span className="dim">{item.old_regime}</span> → <span className="acc">{item.new_regime}</span>
          {item.confidence != null && <> · <span className="dim">conf {Number(item.confidence).toFixed(2)}</span></>}
        </div>
        {item.wiki_path && (
          <div className="dim" style={{ fontFamily: 'var(--mono)', fontSize: 10, marginTop: 6 }}>
            wiki: {item.wiki_path}
          </div>
        )}
      </div>
    )
  }
  if (t === 'wiki' || t === 'strat' || t === 'lesson') {
    return (
      <div className="wiki-body">
        <div className="dim" style={{ fontFamily: 'var(--mono)', fontSize: 10, marginBottom: 10, letterSpacing: '0.04em' }}>
          {item.path} · {item.category} · {item.word_count}w
        </div>
        <WikiBody path={item.path} />
      </div>
    )
  }
  return null
}

export default function KnowledgeFeed() {
  const [type, setType] = useState('all')
  const [expanded, setExpanded] = useState(null)
  const { data: wiki, loading: lwiki } = useApi('/api/wiki/documents?limit=300', 120000)
  const { data: posts, loading: lposts } = useApi('/api/post-mortems?limit=100', 60000)
  const { data: regimes, loading: lregs } = useApi('/api/regime-shifts?limit=100', 60000)

  const items = useMemo(() => {
    const out = []
    for (const w of wiki || []) {
      out.push({
        _type: categoryToType(w.category),
        _id: `w-${w.id}`,
        title: w.title || w.path,
        sub: `${w.category} · ${w.word_count}w · ${w.confidence || '—'}`,
        when: tsOf(w),
        path: w.path,
        category: w.category,
        word_count: w.word_count,
      })
    }
    for (const p of posts || []) {
      out.push({
        _type: 'post',
        _id: `p-${p.id}`,
        title: `post-mortem · ${p.strategy_id || '—'} · ${p.symbol || '—'}`,
        sub: `${p.trigger_type || '—'} · pnl ${p.pnl != null ? `$${Number(p.pnl).toFixed(2)}` : '—'}`,
        when: tsOf(p),
        ...p,
      })
    }
    for (const r of regimes || []) {
      out.push({
        _type: 'regime',
        _id: `r-${r.id}`,
        title: `regime shift · ${r.market} · ${r.old_regime} → ${r.new_regime}`,
        sub: `confidence ${r.confidence != null ? Number(r.confidence).toFixed(2) : '—'}`,
        when: tsOf(r),
        ...r,
      })
    }
    out.sort((a, b) => b.when - a.when)
    return out
  }, [wiki, posts, regimes])

  const filtered = type === 'all' ? items : items.filter((i) => i._type === type)
  const loading = lwiki || lposts || lregs

  return (
    <Card
      title={<><b>knowledge feed</b></>}
      meta={`${filtered.length} entries${type !== 'all' ? ` · ${type}` : ''}`}
      p0
    >
      <div style={{ display: 'flex', gap: 6, padding: '10px 16px', borderBottom: '1px solid var(--line)' }}>
        {TYPES.map((t) => (
          <button
            key={t}
            onClick={() => setType(t)}
            style={{
              fontSize: 10, padding: '3px 9px',
              background: type === t ? 'var(--bg-3)' : 'transparent',
              border: '1px solid var(--line)',
              color: type === t ? 'var(--ink)' : 'var(--ink-3)',
              cursor: 'pointer', letterSpacing: '0.12em', textTransform: 'uppercase',
            }}
          >
            {t}
          </button>
        ))}
      </div>
      {loading && filtered.length === 0 ? (
        <div className="empty-state"><div className="empty-state-title">loading knowledge</div></div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">no entries</div>
          <div className="empty-state-hint">{type === 'all' ? 'wiki + post-mortems + regime shifts will populate here.' : `no ${type} entries yet.`}</div>
        </div>
      ) : (
        <div className="know">
          {filtered.slice(0, 80).map((item) => {
            const open = expanded === item._id
            return (
              <div key={item._id}>
                <div
                  className={`it ${open ? 'sel' : ''}`}
                  onClick={() => setExpanded(open ? null : item._id)}
                >
                  <Pill variant={item._type}>{item._type}</Pill>
                  <div>
                    <div className="title">{item.title}</div>
                    <div className="sub">{item.sub}</div>
                  </div>
                  <span className="when">{fmtWhen(item.when)}</span>
                </div>
                {open && <ExpandedDetail item={item} />}
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}
