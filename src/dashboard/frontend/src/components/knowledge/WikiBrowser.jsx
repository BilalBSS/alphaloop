import { useState, useEffect, useMemo } from 'react'
import { useApi } from '../../hooks/useApi'
import EmptyState from '../EmptyState'

const CATEGORIES = ['all', 'regimes', 'post-mortems', 'strategies', 'evolution', 'symbols', 'meta', 'archive']

// / sort options — updated desc (default), highest confidence, alphabetical
const SORT_OPTIONS = [
  { key: 'updated_desc', label: 'Recently updated' },
  { key: 'confidence_desc', label: 'Highest confidence' },
  { key: 'alpha', label: 'A-Z' },
]

// / confidence tiers — backends vary, so accept any string and bucket loosely
const CONFIDENCE_TIERS = ['all', 'established', 'emerging', 'stub']

function confidenceBucket(raw) {
  if (!raw) return 'unknown'
  const s = String(raw).toLowerCase()
  if (s.includes('estab') || s.includes('high')) return 'established'
  if (s.includes('emerg') || s.includes('medium') || s === 'mid') return 'emerging'
  if (s.includes('stub') || s.includes('low') || s === 'seed') return 'stub'
  return s
}

function parseTs(d) {
  const raw = d.updated_at || d.modified_at || d.created_at || null
  return raw ? new Date(raw).getTime() : 0
}

// / loose confidence → numeric rank (higher = more established)
function confidenceRank(raw) {
  const b = confidenceBucket(raw)
  if (b === 'established') return 3
  if (b === 'emerging') return 2
  if (b === 'stub') return 1
  return 0
}

export default function WikiBrowser() {
  const [category, setCategory] = useState('all')
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('updated_desc')
  const [confidenceFilter, setConfidenceFilter] = useState('all')
  const listUrl = category === 'all'
    ? '/api/wiki/documents?limit=300'
    : `/api/wiki/documents?category=${encodeURIComponent(category)}&limit=300`
  const { data: docs, loading } = useApi(listUrl)
  const [selected, setSelected] = useState(null)

  const filtered = useMemo(() => {
    if (!docs) return []
    let list = docs
    // / text filter
    if (query.trim() !== '') {
      const q = query.toLowerCase()
      list = list.filter(d =>
        (d.path || '').toLowerCase().includes(q) ||
        (d.title || '').toLowerCase().includes(q)
      )
    }
    // / confidence filter
    if (confidenceFilter !== 'all') {
      list = list.filter(d => confidenceBucket(d.confidence) === confidenceFilter)
    }
    // / sort
    if (sort === 'updated_desc') {
      list = [...list].sort((a, b) => parseTs(b) - parseTs(a))
    } else if (sort === 'confidence_desc') {
      list = [...list].sort((a, b) => confidenceRank(b.confidence) - confidenceRank(a.confidence))
    } else if (sort === 'alpha') {
      list = [...list].sort((a, b) => (a.path || '').localeCompare(b.path || ''))
    }
    return list
  }, [docs, query, sort, confidenceFilter])

  return (
    <div className="flex gap-4 h-[70vh]">
      {/* sidebar: category filter + doc list */}
      <aside className="w-80 shrink-0 flex flex-col border border-border rounded overflow-hidden">
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
          {/* sort chips */}
          <div className="flex flex-wrap gap-1">
            {SORT_OPTIONS.map(o => (
              <button
                key={o.key}
                onClick={() => setSort(o.key)}
                className={`filter-chip ${sort === o.key ? 'active' : ''}`}
              >
                {o.label}
              </button>
            ))}
          </div>
          {/* confidence filter chips */}
          <div className="flex flex-wrap gap-1">
            {CONFIDENCE_TIERS.map(t => (
              <button
                key={t}
                onClick={() => setConfidenceFilter(t)}
                className={`filter-chip ${confidenceFilter === t ? 'active' : ''}`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        <ul className="flex-1 overflow-y-auto text-xs">
          {loading && <li className="p-2 text-text-muted">loading…</li>}
          {!loading && filtered.length === 0 && (
            <li className="p-2 text-text-muted">
              {query ? `no matches for "${query}"` :
               confidenceFilter !== 'all' ? `no ${confidenceFilter} docs in this category` :
               'no documents'}
            </li>
          )}
          {!loading && filtered.map((d) => {
            const bucket = confidenceBucket(d.confidence)
            const chipCls = bucket === 'established' ? 'chip-positive' :
                            bucket === 'emerging' ? 'chip-warning' :
                            bucket === 'stub' ? 'chip-neutral' : 'chip-neutral'
            return (
              <li key={d.id}>
                <button
                  onClick={() => setSelected(d.path)}
                  className={`w-full text-left px-2 py-1.5 border-b border-border/50 transition-colors
                    ${selected === d.path ? 'bg-bg-primary text-accent' : 'text-text-secondary hover:bg-bg-primary/50 hover:text-text-primary'}`}
                >
                  <div className="font-mono truncate">{d.path}</div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[10px] text-text-muted">{d.category} · {d.word_count}w</span>
                    <span className={`chip ${chipCls}`}>{d.confidence || '—'}</span>
                  </div>
                </button>
              </li>
            )
          })}
        </ul>
      </aside>

      {/* content pane */}
      <section className="flex-1 border border-border rounded overflow-hidden flex flex-col">
        {selected ? (
          <WikiDocument path={selected} />
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <EmptyState
              title="Select a document"
              hint="Pick a doc on the left. Most recently updated docs rise to the top by default."
            />
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

  if (error) return <div className="p-3 pnl-negative text-sm">error: {error}</div>
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
