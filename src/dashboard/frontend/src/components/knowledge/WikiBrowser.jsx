import { useState, useEffect } from 'react'
import { useApi } from '../../hooks/useApi'

const CATEGORIES = ['all', 'regimes', 'post-mortems', 'strategies', 'evolution', 'symbols', 'meta', 'archive']

export default function WikiBrowser() {
  const [category, setCategory] = useState('all')
  const [query, setQuery] = useState('')
  const listUrl = category === 'all'
    ? '/api/wiki/documents?limit=300'
    : `/api/wiki/documents?category=${encodeURIComponent(category)}&limit=300`
  const { data: docs, loading } = useApi(listUrl)
  const [selected, setSelected] = useState(null)

  // / client-side path + title fuzzy filter; keeps behavior snappy across 78+ docs
  // / without a round-trip. The /api/wiki/search pgvector endpoint is preserved for
  // / a future semantic-search UI but this covers the "find a symbol" case.
  const filtered = !docs
    ? []
    : query.trim() === ''
      ? docs
      : docs.filter((d) => {
          const q = query.toLowerCase()
          return (d.path || '').toLowerCase().includes(q)
            || (d.title || '').toLowerCase().includes(q)
        })

  return (
    <div className="flex gap-3 h-[70vh]">
      {/* sidebar: category filter + doc list */}
      <aside className="w-72 shrink-0 flex flex-col border border-border rounded">
        <div className="p-2 border-b border-border space-y-2">
          <select
            value={category}
            onChange={(e) => { setCategory(e.target.value); setSelected(null) }}
            className="w-full text-xs bg-bg-primary text-text-primary border border-border rounded px-2 py-1"
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="search path or title..."
            className="w-full text-xs bg-bg-primary text-text-primary border border-border rounded px-2 py-1 placeholder:text-text-muted"
          />
        </div>
        <ul className="flex-1 overflow-y-auto text-xs">
          {loading && <li className="p-2 text-text-muted">loading…</li>}
          {!loading && filtered.length === 0 && (
            <li className="p-2 text-text-muted">
              {query ? `no matches for "${query}"` : 'no documents'}
            </li>
          )}
          {!loading && filtered.map((d) => (
            <li key={d.id}>
              <button
                onClick={() => setSelected(d.path)}
                className={`w-full text-left px-2 py-1.5 border-b border-border/50
                  ${selected === d.path ? 'bg-bg-primary text-accent' : 'text-text-secondary hover:bg-bg-primary/50 hover:text-text-primary'}`}
              >
                <div className="font-mono truncate">{d.path}</div>
                <div className="text-[10px] text-text-muted">
                  {d.category} · {d.word_count}w · {d.confidence}
                </div>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      {/* content pane */}
      <section className="flex-1 border border-border rounded overflow-hidden flex flex-col">
        {selected ? (
          <WikiDocument path={selected} />
        ) : (
          <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
            select a document
          </div>
        )}
      </section>
    </div>
  )
}

function WikiDocument({ path }) {
  const [content, setContent] = useState(null)
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setContent(null); setMeta(null); setError(null)
    fetch(`/api/wiki/document?path=${encodeURIComponent(path)}`)
      .then((r) => r.ok ? r.json() : Promise.reject(`${r.status}`))
      .then((j) => { if (!cancelled) { setContent(j.content); setMeta({ title: j.title, category: j.category }) } })
      .catch((e) => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [path])

  if (error) return <div className="p-3 text-loss text-sm">error: {error}</div>
  if (content === null) return <div className="p-3 text-text-muted text-sm">loading…</div>

  return (
    <>
      <header className="px-3 py-2 border-b border-border bg-bg-primary text-xs">
        <div className="text-text-secondary font-mono">{path}</div>
        {meta?.title && <div className="text-text-primary mt-0.5">{meta.title}</div>}
      </header>
      <pre className="flex-1 overflow-auto p-3 text-xs whitespace-pre-wrap leading-relaxed text-text-primary">
        {content}
      </pre>
    </>
  )
}
