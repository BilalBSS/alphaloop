import { useApi } from '../../hooks/useApi'

// / per-provider activity tiles

const PROVIDERS = [
  { key: 'groq',     label: 'groq · llama-3.3-70b' },
  { key: 'cerebras', label: 'cerebras · gpt-oss-120b' },
  { key: 'deepseek', label: 'deepseek · v4' },
  { key: 'ollama',   label: 'ollama · nomic-embed' },
]

function bucketFor(srcKey) {
  const s = (srcKey || '').toLowerCase()
  if (s.includes('groq')) return 'groq'
  if (s.includes('cerebras')) return 'cerebras'
  if (s.includes('deepseek')) return 'deepseek'
  if (s.includes('ollama')) return 'ollama'
  return null
}

export default function LLMLatencyTiles({ health }) {
  const { data: costs } = useApi('/api/costs', 60000)

  // / aggregate by provider
  const callsByProvider = {}
  const rows = Array.isArray(costs) ? costs : (costs?.costs || costs?.providers || [])
  for (const r of rows) {
    const b = bucketFor(r.source)
    if (!b) continue
    callsByProvider[b] = (callsByProvider[b] || 0) + (parseInt(r.call_count) || 0)
  }

  const sources = health?.sources || {}

  return (
    <div className="grid c4">
      {PROVIDERS.map((p) => {
        const calls = callsByProvider[p.key] || 0
        const src = sources[p.key]
        const errs = src?.errors_24h || 0
        const tone = errs > 5 ? 'neg' : errs > 0 ? 'warn' : 'pos'
        const subText = errs === 0 ? (calls > 0 ? `${calls.toLocaleString()} calls total` : 'idle') : `${errs} err 24h`
        return (
          <div key={p.key} className="tile">
            <div className="lab">{p.label}</div>
            <div className={`v ${tone}`}>{calls.toLocaleString()}</div>
            <div className={`x ${tone}`}>{subText}</div>
          </div>
        )
      })}
    </div>
  )
}
