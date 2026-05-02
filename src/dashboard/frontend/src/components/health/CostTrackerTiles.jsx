import { useApi } from '../../hooks/useApi'

// / monthly spend tiles

function totalUsd(data) {
  if (data?.total_usd != null) return Number(data.total_usd) || 0
  const rows = Array.isArray(data) ? data : (data?.costs || data?.providers || [])
  return rows.reduce((s, r) => s + (parseFloat(r.estimated_cost_usd ?? r.usd) || 0), 0)
}

export default function CostTrackerTiles() {
  const { data } = useApi('/api/costs', 60000)
  const llmUsd = totalUsd(data)
  const llmTone = llmUsd > 20 ? 'neg' : llmUsd > 5 ? 'warn' : 'pos'
  const llmText = llmUsd > 0 ? 'paid usage' : 'all free tier'

  return (
    <div className="grid c4">
      <div className="tile">
        <div className="lab">VPS</div>
        <div className="v">$8.00</div>
        <div className="x">fixed · 8GB RAM</div>
      </div>
      <div className="tile">
        <div className="lab">LLM calls</div>
        <div className={`v ${llmTone}`}>${llmUsd.toFixed(2)}</div>
        <div className={`x ${llmTone}`}>{llmText}</div>
      </div>
      <div className="tile">
        <div className="lab">postgres</div>
        <div className="v pos">$0.00</div>
        <div className="x">local on VPS</div>
      </div>
      <div className="tile">
        <div className="lab">tunnel</div>
        <div className="v pos">$0.00</div>
        <div className="x">cloudflare</div>
      </div>
    </div>
  )
}
