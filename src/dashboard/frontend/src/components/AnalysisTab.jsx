import { useState, useMemo } from 'react'
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { useApi } from '../hooks/useApi'
import Panel from './Panel'
import { SkeletonTable, SkeletonChart } from './Skeleton'

// / tooltip style shared across charts
const TIP = { background: '#12121a', border: '1px solid #1e1e2a', fontSize: 12 }

// / color helpers
function scoreColor(v) {
  const n = parseFloat(v || 0)
  if (n >= 70) return 'text-profit'
  if (n >= 40) return 'text-warning'
  return 'text-loss'
}

function consensusBadge(c) {
  const map = { bullish: 'text-profit', bearish: 'text-loss', neutral: 'text-warning', disagree: 'text-accent' }
  return (
    <span className={`px-2 py-0.5 text-xs font-semibold uppercase ${map[c] || 'text-text-muted'}`}>
      {c || '--'}
    </span>
  )
}

function regimeBadge(r) {
  const map = { bull: 'text-profit', bear: 'text-loss', sideways: 'text-warning', high_vol: 'text-accent' }
  return (
    <span className={`px-2 py-0.5 text-xs font-semibold uppercase ${map[r] || 'text-text-muted'}`}>
      {r || '--'}
    </span>
  )
}

// / daily synthesis panel for list view
function SynthesisPanel({ onSelect }) {
  const { data, loading } = useApi('/api/synthesis', 120000)

  if (loading && !data) return <SkeletonTable rows={3} cols={2} />

  if (!data || !data.date) {
    return <div className="text-text-muted text-sm py-2">No synthesis yet. The reasoner runs daily at 5PM ET.</div>
  }

  const buys = data.top_buys || []
  const avoids = data.top_avoids || []
  const dateStr = data.date?.split('T')[0] || data.date

  return (
    <div className="space-y-3">
      <div className="text-[11px] uppercase text-text-secondary">
        Daily Synthesis — {dateStr} (5:00 PM ET)
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <div className="text-[11px] uppercase text-profit mb-1">Top Buys</div>
          {buys.length > 0 ? buys.map((b, i) => (
            <div key={i}
              onClick={() => onSelect(b.symbol || b)}
              className="flex justify-between text-xs py-0.5 cursor-pointer hover:text-accent"
            >
              <span className="font-mono">{i + 1}. {b.symbol || b}</span>
              {b.score != null && <span className="text-profit font-mono">+{parseFloat(b.score).toFixed(1)}</span>}
            </div>
          )) : <div className="text-text-muted text-xs">--</div>}
        </div>
        <div>
          <div className="text-[11px] uppercase text-loss mb-1">Top Avoids</div>
          {avoids.length > 0 ? avoids.map((a, i) => (
            <div key={i}
              onClick={() => onSelect(a.symbol || a)}
              className="flex justify-between text-xs py-0.5 cursor-pointer hover:text-accent"
            >
              <span className="font-mono">{i + 1}. {a.symbol || a}</span>
              {a.score != null && <span className="text-loss font-mono">{parseFloat(a.score).toFixed(1)}</span>}
            </div>
          )) : <div className="text-text-muted text-xs">--</div>}
        </div>
      </div>
      {data.portfolio_risk && (
        <div className="text-xs text-warning">Risk: {data.portfolio_risk}</div>
      )}
    </div>
  )
}

// / symbol list view
function SymbolList({ symbols, loading, onSelect }) {
  const [filter, setFilter] = useState('')

  const filtered = useMemo(() => {
    if (!symbols) return []
    const q = filter.toLowerCase()
    return symbols.filter(s => s.symbol.toLowerCase().includes(q))
  }, [symbols, filter])

  if (loading) return <SkeletonTable rows={8} cols={4} />

  return (
    <div>
      <input
        type="text"
        value={filter}
        onChange={e => setFilter(e.target.value)}
        placeholder="filter symbols..."
        className="w-full bg-bg-primary border border-border px-3 py-2 text-sm text-text-primary
          placeholder:text-text-muted mb-2 outline-none focus:border-accent"
      />
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary text-[11px] uppercase">
            <th className="text-left px-2 py-1">Symbol</th>
            <th className="text-right px-2 py-1">Score</th>
            <th className="text-center px-2 py-1">AI</th>
            <th className="text-center px-2 py-1">Regime</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map(s => (
            <tr
              key={s.symbol}
              onClick={() => onSelect(s.symbol)}
              className="hover:bg-bg-hover border-t border-border cursor-pointer"
              style={{ height: 36 }}
            >
              <td className="px-2 py-1 font-mono font-semibold">{s.symbol}</td>
              <td className={`px-2 py-1 text-right font-mono ${scoreColor(s.composite_score)}`}>
                {parseFloat(s.composite_score || 0).toFixed(1)}
              </td>
              <td className="px-2 py-1 text-center">{consensusBadge(s.ai_consensus)}</td>
              <td className="px-2 py-1 text-center">{regimeBadge(s.regime)}</td>
            </tr>
          ))}
          {filtered.length === 0 && (
            <tr><td colSpan={4} className="text-text-muted text-sm py-4 text-center">No symbols match</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

// / score overview badges
function ScoreOverview({ score }) {
  if (!score) return <div className="text-text-muted text-sm py-2">No analysis data</div>
  const details = typeof score.details === 'object' ? score.details : {}
  return (
    <div className="flex flex-wrap gap-4 items-center">
      <div>
        <div className="text-[11px] uppercase text-text-secondary">Composite</div>
        <div className={`text-2xl font-mono font-bold ${scoreColor(score.composite_score)}`}>
          {parseFloat(score.composite_score || 0).toFixed(1)}
        </div>
      </div>
      <div>
        <div className="text-[11px] uppercase text-text-secondary">Fundamental</div>
        <div className={`text-lg font-mono ${scoreColor(score.fundamental_score)}`}>
          {parseFloat(score.fundamental_score || 0).toFixed(1)}
        </div>
      </div>
      <div>
        <div className="text-[11px] uppercase text-text-secondary">AI Consensus</div>
        {consensusBadge(details.ai_consensus || score.ai_consensus)}
      </div>
      <div>
        <div className="text-[11px] uppercase text-text-secondary">Regime</div>
        {regimeBadge(score.regime)}
      </div>
    </div>
  )
}

// / 60-day price chart
function PriceChart({ priceHistory }) {
  if (!priceHistory || priceHistory.length === 0) {
    return <div className="flex items-center justify-center h-48 text-text-muted text-sm">No price data</div>
  }
  const data = priceHistory.slice().reverse().map(d => ({
    date: d.date?.split('T')[0] || d.date,
    close: parseFloat(d.close || 0),
  }))
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data}>
        <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#8888a0' }} interval="preserveStartEnd" />
        <YAxis domain={['auto', 'auto']} tick={{ fontSize: 10, fill: '#8888a0' }} width={60}
          tickFormatter={v => `$${v}`} />
        <Tooltip contentStyle={TIP} labelStyle={{ color: '#8888a0' }}
          formatter={v => [`$${v.toFixed(2)}`, 'Close']} />
        <Line type="monotone" dataKey="close" stroke="#3b82f6" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  )
}

// / fundamentals table with sector comparison
function FundamentalsPanel({ fundamentals }) {
  if (!fundamentals) return <div className="text-text-muted text-sm py-2">No fundamentals data</div>
  const rows = [
    { label: 'P/E', val: fundamentals.pe_ratio, sector: fundamentals.sector_pe_avg, lower: true },
    { label: 'P/S', val: fundamentals.ps_ratio, sector: fundamentals.sector_ps_avg, lower: true },
    { label: 'PEG', val: fundamentals.peg_ratio, sector: null, lower: true },
    { label: 'FCF Margin', val: fundamentals.fcf_margin, sector: null, lower: false, pct: true },
    { label: 'D/E', val: fundamentals.debt_to_equity, sector: null, lower: true },
    { label: 'Rev Growth 1Y', val: fundamentals.revenue_growth_1y, sector: null, lower: false, pct: true },
  ]
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-text-secondary text-[11px] uppercase">
          <th className="text-left px-2 py-1">Metric</th>
          <th className="text-right px-2 py-1">Value</th>
          <th className="text-right px-2 py-1">Sector</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(r => {
          const v = parseFloat(r.val || 0)
          const s = r.sector ? parseFloat(r.sector) : null
          const better = s !== null ? (r.lower ? v < s : v > s) : null
          return (
            <tr key={r.label} className="border-t border-border" style={{ height: 32 }}>
              <td className="px-2 py-1 text-text-secondary">{r.label}</td>
              <td className={`px-2 py-1 text-right font-mono ${better === true ? 'text-profit' : better === false ? 'text-loss' : ''}`}>
                {r.pct ? `${(v * 100).toFixed(1)}%` : v.toFixed(2)}
              </td>
              <td className="px-2 py-1 text-right font-mono text-text-muted">
                {s !== null ? s.toFixed(2) : '--'}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// / dcf valuation panel
function DcfPanel({ dcf }) {
  if (!dcf) return <div className="text-text-muted text-sm py-2">No DCF data</div>
  const p10 = parseFloat(dcf.fair_value_p10 || 0)
  const median = parseFloat(dcf.fair_value_median || 0)
  const p90 = parseFloat(dcf.fair_value_p90 || 0)
  const current = parseFloat(dcf.current_price || 0)
  const upside = parseFloat(dcf.upside_pct || 0)

  // / range bar: position markers between p10 and p90
  const range = p90 - p10
  const medianPct = range > 0 ? ((median - p10) / range) * 100 : 50
  const currentPct = range > 0 ? Math.min(100, Math.max(0, ((current - p10) / range) * 100)) : 50

  return (
    <div className="space-y-3">
      <div className="flex justify-between text-xs text-text-secondary">
        <span>P10: ${p10.toFixed(0)}</span>
        <span className="font-semibold text-text-primary">Median: ${median.toFixed(0)}</span>
        <span>P90: ${p90.toFixed(0)}</span>
      </div>
      {/* range bar */}
      <div className="relative h-3 bg-bg-primary rounded">
        <div className="absolute h-full bg-accent/20 rounded" style={{ left: 0, right: 0 }} />
        <div className="absolute top-0 h-full w-0.5 bg-accent" style={{ left: `${medianPct}%` }}
          title={`Median: $${median.toFixed(0)}`} />
        <div className="absolute top-0 h-full w-0.5 bg-warning" style={{ left: `${currentPct}%` }}
          title={`Current: $${current.toFixed(0)}`} />
      </div>
      <div className="flex justify-between text-xs">
        <span className="text-text-muted">Current: <span className="font-mono">${current.toFixed(2)}</span></span>
        <span className={`font-mono font-semibold ${upside >= 0 ? 'text-profit' : 'text-loss'}`}>
          {upside >= 0 ? '+' : ''}{(upside * 100).toFixed(1)}% upside
        </span>
      </div>
      <div className="text-xs text-text-muted">
        Confidence: <span className="uppercase font-semibold">{dcf.dcf_confidence || '--'}</span>
        {' '}({dcf.num_simulations || '10k'} simulations)
      </div>
    </div>
  )
}

// / insider activity table
function InsiderPanel({ insiderTrades }) {
  if (!insiderTrades || insiderTrades.length === 0) {
    return <div className="text-text-muted text-sm py-2">No insider activity</div>
  }
  const typeColor = { buy: 'text-profit', sell: 'text-loss', option_exercise: 'text-warning' }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-text-secondary text-[11px] uppercase">
          <th className="text-left px-2 py-1">Date</th>
          <th className="text-left px-2 py-1">Name</th>
          <th className="text-left px-2 py-1">Type</th>
          <th className="text-right px-2 py-1">Value</th>
        </tr>
      </thead>
      <tbody>
        {insiderTrades.slice(0, 10).map((t, i) => (
          <tr key={i} className="border-t border-border" style={{ height: 32 }}>
            <td className="px-2 py-1 text-text-muted">{t.filing_date?.split('T')[0] || '--'}</td>
            <td className="px-2 py-1 truncate max-w-[100px]" title={`${t.insider_name} (${t.insider_title})`}>
              {t.insider_name}
            </td>
            <td className={`px-2 py-1 uppercase ${typeColor[t.transaction_type] || ''}`}>
              {t.transaction_type}
            </td>
            <td className="px-2 py-1 text-right font-mono">
              ${parseFloat(t.total_value || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// / sentiment panel: news bar chart + social summary
function SentimentPanel({ sentiment, socialSentiment }) {
  const hasNews = sentiment && sentiment.length > 0
  const latestSocial = socialSentiment && socialSentiment.length > 0 ? socialSentiment[0] : null

  const chartData = hasNews
    ? sentiment.slice().reverse().map(s => ({
        date: s.date?.split('T')[0] || s.date,
        score: parseFloat(s.sentiment_score || 0),
      }))
    : []

  return (
    <div className="space-y-3">
      {hasNews ? (
        <ResponsiveContainer width="100%" height={140}>
          <BarChart data={chartData}>
            <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#8888a0' }} interval="preserveStartEnd" />
            <YAxis domain={[-1, 1]} tick={{ fontSize: 9, fill: '#8888a0' }} width={30} />
            <Tooltip contentStyle={TIP} formatter={v => [v.toFixed(3), 'Sentiment']} />
            <Bar dataKey="score">
              {chartData.map((d, i) => (
                <Cell key={i} fill={d.score >= 0 ? '#00dc82' : '#ff4757'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      ) : (
        <div className="text-text-muted text-sm py-2">No news sentiment data</div>
      )}
      {latestSocial && (
        <div className="text-xs text-text-secondary">
          StockTwits:{' '}
          <span className="text-profit font-mono">{(parseFloat(latestSocial.bullish_pct || 0) * 100).toFixed(0)}%</span>
          {' '}bull /{' '}
          <span className="text-loss font-mono">{(parseFloat(latestSocial.bearish_pct || 0) * 100).toFixed(0)}%</span>
          {' '}bear
          <span className="text-text-muted ml-2">({latestSocial.volume || 0} posts)</span>
        </div>
      )}
    </div>
  )
}

// / ai analysis: dual-llm stacked vertical
function AiAnalysisPanel({ score }) {
  const details = (score?.details && typeof score.details === 'object') ? score.details : {}
  const consensus = details.ai_consensus || '--'
  const groqSignal = details.llm_signal_groq
  const deepseekSignal = details.llm_signal_deepseek
  const groqText = details.llm_analysis_groq
  const deepseekText = details.llm_analysis_deepseek

  const signalColor = s => s === 'bullish' ? 'text-profit' : s === 'bearish' ? 'text-loss' : 'text-warning'

  if (!groqText && !deepseekText) {
    return <div className="text-text-muted text-sm py-2">AI analysis not yet available for this symbol.</div>
  }

  return (
    <div className="space-y-3">
      <div className="text-xs">Consensus: {consensusBadge(consensus)}</div>
      <div className="space-y-3">
        <div className="bg-bg-primary p-4 border border-border">
          <div className="text-[11px] uppercase text-text-secondary mb-2">
            Groq (Llama 3.1 8b)
            {groqSignal && <span className={`ml-2 font-semibold ${signalColor(groqSignal)}`}>{groqSignal}</span>}
          </div>
          {groqText
            ? <div className="text-sm text-text-primary whitespace-pre-wrap leading-relaxed">{groqText}</div>
            : <div className="text-text-muted text-sm">Pending. Next cycle in ~30 min.</div>
          }
        </div>
        <div className="bg-bg-primary p-4 border border-border">
          <div className="text-[11px] uppercase text-text-secondary mb-2">
            DeepSeek V3.2
            {deepseekSignal && <span className={`ml-2 font-semibold ${signalColor(deepseekSignal)}`}>{deepseekSignal}</span>}
          </div>
          {deepseekText
            ? <div className="text-sm text-text-primary whitespace-pre-wrap leading-relaxed">{deepseekText}</div>
            : <div className="text-text-muted text-sm">Pending. Next cycle in ~60 min.</div>
          }
        </div>
      </div>
    </div>
  )
}

// / evolution history table
function EvolutionPanel({ evolution }) {
  if (!evolution || evolution.length === 0) {
    return <div className="text-text-muted text-sm py-2">No evolution events for this symbol.</div>
  }
  const actionColor = {
    spawn: 'text-accent', spawn_tier2: 'text-accent', mutate: 'text-accent',
    kill: 'text-loss', promote: 'text-profit', graduate: 'text-profit',
  }
  return (
    <div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary text-[11px] uppercase">
            <th className="text-left px-2 py-1">Gen</th>
            <th className="text-left px-2 py-1">Action</th>
            <th className="text-left px-2 py-1">Strategy</th>
            <th className="text-right px-2 py-1">Date</th>
          </tr>
        </thead>
        <tbody>
          {evolution.map((e, i) => (
            <tr key={i} className="border-t border-border" style={{ height: 32 }}>
              <td className="px-2 py-1 font-mono">{e.generation}</td>
              <td className={`px-2 py-1 uppercase ${actionColor[e.action] || 'text-text-secondary'}`}>
                {e.action}
              </td>
              <td className="px-2 py-1 font-mono truncate max-w-[120px]" title={e.strategy_id}>
                {e.strategy_id}
              </td>
              <td className="px-2 py-1 text-right text-text-muted">
                {e.created_at?.split('T')[0] || '--'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {evolution[0]?.details && typeof evolution[0].details === 'object' && evolution[0].details.tier && (
        <div className="text-xs text-text-secondary mt-2 px-2">
          Tier: <span className="uppercase font-semibold">{evolution[0].details.tier}</span>
          {evolution[0].details.sector && (
            <span className="text-text-muted"> (from {evolution[0].details.sector} sector base)</span>
          )}
        </div>
      )}
    </div>
  )
}

// / trade history table
function TradeHistoryPanel({ trades }) {
  if (!trades || trades.length === 0) {
    return <div className="text-text-muted text-sm py-2">No trades for this symbol</div>
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-text-secondary text-[11px] uppercase">
          <th className="text-left px-2 py-1">Side</th>
          <th className="text-right px-2 py-1">Qty</th>
          <th className="text-right px-2 py-1">Price</th>
          <th className="text-right px-2 py-1">P&L</th>
          <th className="text-right px-2 py-1">Date</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t, i) => {
          const pnl = parseFloat(t.pnl || 0)
          return (
            <tr key={i} className="border-t border-border" style={{ height: 32 }}>
              <td className={`px-2 py-1 ${t.side === 'buy' ? 'text-profit' : 'text-loss'}`}>
                {t.side?.toUpperCase()}
              </td>
              <td className="px-2 py-1 text-right font-mono">{t.qty}</td>
              <td className="px-2 py-1 text-right font-mono">${parseFloat(t.price || 0).toFixed(2)}</td>
              <td className={`px-2 py-1 text-right font-mono ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                {pnl !== 0 ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}` : '--'}
              </td>
              <td className="px-2 py-1 text-right text-text-muted">{t.created_at?.split('T')[0] || '--'}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// / signals + strategy breakdown
function SignalsPanel({ signals }) {
  if (!signals || signals.length === 0) {
    return <div className="text-text-muted text-sm py-2">No signals for this symbol</div>
  }

  // / compute strategy breakdown from signals in frontend
  const breakdown = useMemo(() => {
    const map = {}
    for (const s of signals) {
      const key = s.strategy_id || 'unknown'
      if (!map[key]) map[key] = { buys: 0, sells: 0, last: s.created_at }
      if (s.signal_type === 'buy') map[key].buys++
      else map[key].sells++
    }
    return Object.entries(map).map(([id, v]) => ({ id, ...v }))
  }, [signals])

  return (
    <div className="space-y-3">
      {/* strategy breakdown */}
      {breakdown.length > 0 && (
        <div>
          <div className="text-[11px] uppercase text-text-secondary mb-1">Strategies Active</div>
          <div className="flex flex-wrap gap-2">
            {breakdown.map(b => (
              <div key={b.id} className="text-xs bg-bg-primary px-2 py-1 border border-border">
                <span className="font-mono">{b.id}</span>
                <span className="text-profit ml-1">{b.buys}B</span>
                {b.sells > 0 && <span className="text-loss ml-1">{b.sells}S</span>}
              </div>
            ))}
          </div>
        </div>
      )}
      {/* recent signals */}
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary text-[11px] uppercase">
            <th className="text-left px-2 py-1">Type</th>
            <th className="text-right px-2 py-1">Strength</th>
            <th className="text-left px-2 py-1">Strategy</th>
            <th className="text-right px-2 py-1">Date</th>
          </tr>
        </thead>
        <tbody>
          {signals.slice(0, 10).map((s, i) => (
            <tr key={i} className="border-t border-border" style={{ height: 32 }}>
              <td className={`px-2 py-1 uppercase ${s.signal_type === 'buy' ? 'text-profit' : 'text-loss'}`}>
                {s.signal_type}
              </td>
              <td className="px-2 py-1 text-right font-mono">
                {parseFloat(s.strength || 0).toFixed(2)}
              </td>
              <td className="px-2 py-1 font-mono truncate max-w-[80px]">{s.strategy_id}</td>
              <td className="px-2 py-1 text-right text-text-muted">{s.created_at?.split('T')[0] || '--'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// / detail view: fetches its own data, keyed on symbol for clean remount
function SymbolDetail({ symbol, onBack }) {
  const { data, loading, error } = useApi(`/api/analysis/${symbol}`, 30000)

  if (loading && !data) {
    return (
      <div className="space-y-2">
        <button onClick={onBack} className="text-accent text-sm hover:underline mb-2">&larr; Back to list</button>
        <SkeletonChart />
        <SkeletonChart />
      </div>
    )
  }

  if (error) {
    return (
      <div>
        <button onClick={onBack} className="text-accent text-sm hover:underline mb-2">&larr; Back to list</button>
        <Panel title={symbol} error={`Failed to load: ${error}`} />
      </div>
    )
  }

  const d = data || {}

  return (
    <div className="space-y-2">
      <button onClick={onBack} className="text-accent text-sm hover:underline">&larr; Back to list</button>
      <div className="text-lg font-mono font-bold text-text-primary">{symbol}</div>

      {/* row 1: score overview */}
      <Panel title="Score Overview">
        <ScoreOverview score={d.score} />
      </Panel>

      {/* row 2: price chart */}
      <Panel title="Price History (60d)">
        <PriceChart priceHistory={d.price_history} />
      </Panel>

      {/* row 3: fundamentals + dcf */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <Panel title="Fundamentals">
          <FundamentalsPanel fundamentals={d.fundamentals} />
        </Panel>
        <Panel title="DCF Valuation">
          <DcfPanel dcf={d.dcf} />
        </Panel>
      </div>

      {/* row 4: sentiment + insider */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <Panel title="Sentiment">
          <SentimentPanel sentiment={d.sentiment} socialSentiment={d.social_sentiment} />
        </Panel>
        <Panel title="Insider Activity">
          <InsiderPanel insiderTrades={d.insider_trades} />
        </Panel>
      </div>

      {/* row 5: ai analysis — stacked vertical */}
      <Panel title="AI Analysis">
        <AiAnalysisPanel score={d.score} />
      </Panel>

      {/* row 6: evolution history */}
      <Panel title="Evolution History">
        <EvolutionPanel evolution={d.evolution} />
      </Panel>

      {/* row 7: trades + signals */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <Panel title="Trade History">
          <TradeHistoryPanel trades={d.trades} />
        </Panel>
        <Panel title="Signals &amp; Strategies">
          <SignalsPanel signals={d.signals} />
        </Panel>
      </div>
    </div>
  )
}

// / main tab: synthesis + symbol list or detail view
export default function AnalysisTab() {
  const [selectedSymbol, setSelectedSymbol] = useState(null)
  const symbols = useApi('/api/symbols', 60000)

  if (selectedSymbol) {
    return (
      <SymbolDetail
        key={selectedSymbol}
        symbol={selectedSymbol}
        onBack={() => setSelectedSymbol(null)}
      />
    )
  }

  return (
    <div className="space-y-2">
      <Panel title="Daily Synthesis">
        <SynthesisPanel onSelect={setSelectedSymbol} />
      </Panel>
      <Panel title="Symbol Analysis">
        <SymbolList
          symbols={symbols.data}
          loading={symbols.loading}
          onSelect={setSelectedSymbol}
        />
      </Panel>
    </div>
  )
}
