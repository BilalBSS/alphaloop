import { useState, useMemo } from 'react'
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { useApi } from '../hooks/useApi'
import Panel from './Panel'
import { SkeletonTable, SkeletonChart } from './Skeleton'

// / tooltip style shared across charts
const TIP = { background: '#12121a', border: '1px solid #1e1e2a', fontSize: 12 }

// / format large numbers to human-readable (currency)
function fmtLargeNum(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '--'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`
  return `${sign}$${abs.toFixed(2)}`
}

// / format large counts without dollar sign (shares, units)
function fmtCount(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '--'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1e12) return `${sign}${(abs / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(1)}K`
  return `${sign}${abs.toLocaleString()}`
}

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

// / strategy evaluation cycle panel (collapsed by default)
function StrategyEvalPanel({ onSelect }) {
  const { data, loading } = useApi('/api/strategy-evaluations?limit=1', 120000)

  if (loading && !data) return <div className="text-text-muted text-sm py-2">Loading...</div>

  const latest = Array.isArray(data) && data.length > 0 ? data[0] : null
  if (!latest) return <div className="text-text-muted text-sm py-2">No evaluation data yet. Strategy agent posts every 5 min.</div>

  const nearMisses = latest.near_misses || []
  const ts = latest.created_at?.split('T')[1]?.slice(0, 5) || ''

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-4 text-xs font-mono">
        <span>{latest.total_pairs} pairs</span>
        <span className="text-profit">{latest.entry_hits} hits</span>
        <span className="text-loss">{latest.blocked_consensus} consensus</span>
        <span className="text-warning">{latest.blocked_threshold} threshold</span>
        <span className={latest.signals_generated > 0 ? 'text-profit font-bold' : ''}>{latest.signals_generated} signals</span>
        {ts && <span className="text-text-muted">{ts} UTC</span>}
      </div>
      {nearMisses.length > 0 && (
        <div>
          <div className="text-[11px] uppercase text-text-secondary mb-1">Near-Misses</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-secondary text-[11px] uppercase">
                <th className="text-left px-2 py-1">Symbol</th>
                <th className="text-right px-2 py-1">Strength</th>
                <th className="text-left px-2 py-1">Block</th>
              </tr>
            </thead>
            <tbody>
              {nearMisses.map((nm, i) => {
                const isConsensus = (nm.block_reason || '').includes('consensus')
                return (
                  <tr
                    key={i}
                    onClick={() => onSelect(nm.symbol)}
                    className={`border-t border-border hover:bg-bg-hover cursor-pointer border-l-2 ${isConsensus ? 'border-l-loss' : 'border-l-warning'}`}
                    style={{ height: 32 }}
                  >
                    <td className="px-2 py-1 font-mono font-semibold">{nm.symbol}</td>
                    <td className={`px-2 py-1 text-right font-mono ${scoreColor(nm.raw_strength * 100)}`}>
                      {parseFloat(nm.raw_strength || 0).toFixed(2)}
                    </td>
                    <td className="px-2 py-1">
                      <span className={`px-2 py-0.5 text-xs font-semibold uppercase ${isConsensus ? 'text-loss' : 'text-warning'}`}>
                        {isConsensus ? 'consensus' : 'threshold'}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
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

// / score overview badges + composite breakdown
function ScoreOverview({ score }) {
  if (!score) return <div className="text-text-muted text-sm py-2">No analysis data</div>
  const details = typeof score.details === 'object' ? score.details : {}

  // / component scores for stacked bar breakdown (all 0-100 scale)
  const components = [
    { key: 'ratio_score_100', label: 'Ratio', weight: 35, color: 'bg-accent' },
    { key: 'dcf_score_100', label: 'DCF', weight: 25, color: 'bg-profit' },
    { key: 'earnings_score_100', label: 'Earnings', weight: 20, color: 'bg-warning' },
    { key: 'insider_score_100', label: 'Insider', weight: 20, color: 'bg-text-secondary' },
  ]
  const hasBreakdown = components.some(c => details[c.key] != null)

  return (
    <div className="space-y-3">
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
      {hasBreakdown && (
        <>
          <div className="flex h-2 w-full overflow-hidden">
            {components.map(c => {
              const raw = parseFloat(details[c.key] || 0)
              const opacity = Math.max(0.15, Math.min(1, raw / 100))
              return (
                <div
                  key={c.key}
                  className={`${c.color} transition-all`}
                  style={{ width: `${c.weight}%`, opacity }}
                  title={`${c.label}: ${raw.toFixed(1)}`}
                />
              )
            })}
          </div>
          <div className="grid grid-cols-4 gap-1 text-[10px]">
            {components.map(c => {
              const raw = details[c.key]
              return (
                <div key={c.key} className="text-center">
                  <div className="text-text-muted">{c.label} ({c.weight}%)</div>
                  <div className="font-mono">{raw != null ? parseFloat(raw).toFixed(1) : '--'}</div>
                </div>
              )
            })}
          </div>
        </>
      )}
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

// / technical indicators panel
function IndicatorsPanel({ symbol }) {
  const { data, loading } = useApi(`/api/indicators/${symbol}?limit=1`, 60000)

  if (loading && !data) return <div className="text-text-muted text-sm py-2">Loading...</div>

  const latest = Array.isArray(data) && data.length > 0 ? data[0] : null
  if (!latest) return <div className="text-text-muted text-sm py-2">No indicator data yet</div>

  const rows = [
    { label: 'RSI (14)', val: latest.rsi14, fmt: v => v?.toFixed(1), color: v => v > 70 ? 'text-loss' : v < 30 ? 'text-profit' : '' },
    { label: 'MACD', val: latest.macd, fmt: v => v?.toFixed(4), color: v => v > 0 ? 'text-profit' : 'text-loss' },
    { label: 'MACD Signal', val: latest.macd_signal, fmt: v => v?.toFixed(4) },
    { label: 'MACD Hist', val: latest.macd_histogram, fmt: v => v?.toFixed(4), color: v => v > 0 ? 'text-profit' : 'text-loss' },
    { label: 'ADX', val: latest.adx, fmt: v => v?.toFixed(1), color: v => v > 25 ? 'text-profit' : 'text-text-muted' },
    { label: 'SMA 20', val: latest.sma20, fmt: v => `$${v?.toFixed(2)}` },
    { label: 'SMA 50', val: latest.sma50, fmt: v => `$${v?.toFixed(2)}` },
    { label: 'BB Upper', val: latest.bb_upper, fmt: v => `$${v?.toFixed(2)}` },
    { label: 'BB Middle', val: latest.bb_middle, fmt: v => `$${v?.toFixed(2)}` },
    { label: 'BB Lower', val: latest.bb_lower, fmt: v => `$${v?.toFixed(2)}` },
    { label: 'ATR (14)', val: latest.atr, fmt: v => v?.toFixed(4) },
  ]

  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-text-secondary text-[11px] uppercase">
          <th className="text-left px-2 py-1">Indicator</th>
          <th className="text-right px-2 py-1">Value</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(r => {
          const v = parseFloat(r.val)
          const display = isNaN(v) ? '--' : r.fmt(v)
          const cls = r.color && !isNaN(v) ? r.color(v) : ''
          return (
            <tr key={r.label} className="border-t border-border" style={{ height: 28 }}>
              <td className="px-2 py-1">{r.label}</td>
              <td className={`px-2 py-1 text-right font-mono ${cls}`}>{display}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// / fundamentals table with sector comparison + edgar raw financials
function FundamentalsPanel({ fundamentals, score }) {
  if (!fundamentals) return <div className="text-text-muted text-sm py-2">No fundamentals data</div>
  const rows = [
    { label: 'P/E', val: fundamentals.pe_ratio, sector: fundamentals.sector_pe_avg, lower: true },
    { label: 'P/S', val: fundamentals.ps_ratio, sector: fundamentals.sector_ps_avg, lower: true },
    { label: 'PEG', val: fundamentals.peg_ratio, sector: null, lower: true },
    { label: 'FCF Margin', val: fundamentals.fcf_margin, sector: fundamentals.sector_fcf_margin_avg, lower: false, pct: true },
    { label: 'D/E', val: fundamentals.debt_to_equity, sector: fundamentals.sector_de_avg, lower: true },
    { label: 'Rev Growth 1Y', val: fundamentals.revenue_growth_1y, sector: fundamentals.sector_rev_growth_avg, lower: false, pct: true },
  ]

  // / edgar raw financials from score details or fundamentals
  const details = (score?.details && typeof score.details === 'object') ? score.details : {}
  const src = fundamentals || {}
  const dataSource = src.data_source || details.data_source
  const edgarRows = [
    { label: 'Revenue', val: src.total_revenue || src.revenue || details.revenue },
    { label: 'Net Income', val: src.net_income || details.net_income },
    { label: 'Free Cash Flow', val: src.free_cash_flow || details.free_cash_flow },
    { label: 'Total Cash', val: src.total_cash || details.total_cash },
    { label: 'Total Debt', val: src.total_debt || details.total_debt },
    { label: 'Net Debt', val: src.net_debt || details.net_debt },
    { label: 'Shares Outstanding', val: src.shares_outstanding || details.shares_outstanding, isCount: true },
  ].filter(r => r.val != null)

  return (
    <div>
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
                  {s !== null ? (r.pct ? `${(s * 100).toFixed(1)}%` : s.toFixed(2)) : '--'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {edgarRows.length > 0 && (
        <div className="border-t border-border mt-2 pt-2">
          <table className="w-full text-[11px]">
            <tbody>
              {edgarRows.map(r => (
                <tr key={r.label} className="border-t border-border first:border-t-0" style={{ height: 26 }}>
                  <td className="px-2 py-0.5 text-text-secondary">{r.label}</td>
                  <td className="px-2 py-0.5 text-right font-mono">{r.isCount ? fmtCount(r.val) : fmtLargeNum(r.val)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {dataSource && (
            <div className="text-[10px] uppercase text-text-muted px-2 pt-1">
              data source: {dataSource}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// / dcf valuation panel
function DcfPanel({ dcf }) {
  if (!dcf || !dcf.fair_value_median) return <div className="text-text-muted text-sm py-2">No DCF data</div>
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

// / insider activity table with aggregate summary + detailed trades
function InsiderPanel({ insiderTrades, score, symbol }) {
  const details = (score?.details && typeof score.details === 'object') ? score.details : {}
  // / fetch richer trades from dedicated endpoint
  const { data: apiTrades } = useApi(`/api/insider/${symbol}`, 60000)
  // / prefer api trades, fall back to analysis data
  const trades = (apiTrades && apiTrades.length > 0) ? apiTrades : insiderTrades
  const hasTradeRows = trades && trades.length > 0

  if (!hasTradeRows) {
    const sig = details.insider_signal
    const str = details.insider_score_100
    if (!sig) return <div className="text-text-muted text-sm py-2">No insider activity</div>
    const c = sig === 'bullish' ? 'border-l-profit text-profit' : sig === 'bearish' ? 'border-l-loss text-loss' : 'border-l-text-secondary text-text-secondary'
    return (
      <div className="space-y-2">
        <div className={`text-xs font-semibold px-2 py-1 border-l-2 ${c}`}>
          {sig} {str != null && <span className="font-mono ml-1">({parseFloat(str).toFixed(0)})</span>}
        </div>
        <div className="text-text-muted text-[10px] px-2">Trade data refreshes at 6AM ET</div>
      </div>
    )
  }

  // / compute aggregates from trades
  const buys = trades.filter(t => t.transaction_type === 'buy')
  const sells = trades.filter(t => t.transaction_type === 'sell')
  const buyTotal = buys.reduce((s, t) => s + parseFloat(t.total_value || 0), 0)
  const sellTotal = sells.reduce((s, t) => s + parseFloat(t.total_value || 0), 0)
  const netBuy = buyTotal > sellTotal
  const insiderStrength = details.insider_score_100

  const fmtVal = v => {
    const n = parseFloat(v)
    if (v == null || isNaN(n) || n === 0) return '--'
    return n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : `$${(n / 1e3).toFixed(0)}K`
  }

  return (
    <div className="space-y-2">
      {/* aggregate summary */}
      <div className={`text-xs font-semibold px-2 py-1 border-l-2 ${netBuy ? 'border-l-profit text-profit' : 'border-l-loss text-loss'}`}>
        {buys.length} buys ({fmtVal(buyTotal)}) / {sells.length} sells ({fmtVal(sellTotal)})
      </div>
      {insiderStrength != null && (
        <div className="px-2">
          <div className="flex items-center gap-2 text-[10px] text-text-secondary">
            <span className={netBuy ? 'text-profit' : 'text-loss'}>{netBuy ? 'Bullish' : 'Bearish'}</span>
            <div className="flex-1 h-1.5 bg-bg-primary rounded overflow-hidden">
              <div className={`h-full ${netBuy ? 'bg-profit' : 'bg-loss'}`}
                style={{ width: `${Math.min(100, parseFloat(insiderStrength))}%` }} />
            </div>
            <span className={`font-mono ${netBuy ? 'text-profit' : 'text-loss'}`}>{netBuy ? '+' : '-'}{parseFloat(insiderStrength).toFixed(0)}</span>
          </div>
        </div>
      )}
      {/* detailed trades table */}
      <div style={{ maxHeight: 288, overflowY: 'auto' }}>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-text-secondary text-[11px] uppercase">
              <th className="text-left px-2 py-1">Date</th>
              <th className="text-left px-2 py-1">Name</th>
              <th className="text-left px-2 py-1">Title</th>
              <th className="text-left px-2 py-1">Type</th>
              <th className="text-right px-2 py-1">Shares</th>
              <th className="text-right px-2 py-1">Value</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => {
              const isBuy = t.transaction_type === 'buy'
              return (
                <tr key={i} className={`border-t border-border border-l-2 ${isBuy ? 'border-l-profit' : 'border-l-loss'}`} style={{ height: 32 }}>
                  <td className="px-2 py-1 text-text-muted whitespace-nowrap">
                    {t.filing_date?.split('T')[0] || '--'}
                  </td>
                  <td className="px-2 py-1 truncate max-w-[100px]" title={t.insider_name}>
                    {t.insider_name || '--'}
                  </td>
                  <td className="px-2 py-1 text-text-muted truncate max-w-[80px]" title={t.insider_title}>
                    {t.insider_title || '--'}
                  </td>
                  <td className={`px-2 py-1 uppercase font-semibold ${isBuy ? 'text-profit' : 'text-loss'}`}>
                    {t.transaction_type || '--'}
                  </td>
                  <td className="px-2 py-1 text-right font-mono">
                    {parseInt(t.shares || 0).toLocaleString()}
                  </td>
                  <td className="px-2 py-1 text-right font-mono">
                    {fmtVal(parseFloat(t.total_value || 0))}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// / sentiment panel: news chart + fear/greed + vix + social detail
function SentimentPanel({ sentiment, socialSentiment, isCrypto, score }) {
  const hasNews = sentiment && sentiment.length > 0
  const latestSocial = socialSentiment && socialSentiment.length > 0 ? socialSentiment[0] : null
  const details = (score?.details && typeof score.details === 'object') ? score.details : {}

  const chartData = hasNews
    ? sentiment.slice().reverse().map(s => ({
        date: s.date?.split('T')[0] || s.date,
        score: parseFloat(s.sentiment_score || 0),
      }))
    : []

  const bullPct = latestSocial ? parseFloat(latestSocial.bullish_pct || 0) : 0
  const bearPct = latestSocial ? parseFloat(latestSocial.bearish_pct || 0) : 0
  const mentions = latestSocial ? parseInt(latestSocial.volume || 0) : 0

  // / fear/greed + vix from score details
  const fng = details.fear_greed_index || details.fear_greed || latestSocial?.raw_score
  const vix = details.vix || details.vix_level

  const fngLabel = v => {
    const n = parseFloat(v)
    if (isNaN(n)) return '--'
    if (n <= 20) return 'Extreme Fear'
    if (n <= 40) return 'Fear'
    if (n <= 60) return 'Neutral'
    if (n <= 80) return 'Greed'
    return 'Extreme Greed'
  }
  const fngColor = v => {
    const n = parseFloat(v)
    if (isNaN(n)) return ''
    if (n <= 30) return 'text-loss'
    if (n <= 60) return 'text-warning'
    return 'text-profit'
  }

  return (
    <div className="space-y-3">
      {/* fear/greed + vix + social detail rows */}
      {(fng != null || vix != null || mentions > 0) && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-[11px]">
          {isCrypto && fng != null && (
            <div className="bg-bg-primary border border-border p-2">
              <div className="text-[10px] uppercase text-text-muted">Crypto F&G</div>
              <div className={`text-lg font-mono font-bold ${fngColor(fng)}`}>{parseFloat(fng).toFixed(0)}</div>
              <div className={`text-[10px] uppercase font-semibold ${fngColor(fng)}`}>{fngLabel(fng)}</div>
            </div>
          )}
          {vix != null && (
            <div className="bg-bg-primary border border-border p-2">
              <div className="text-[10px] uppercase text-text-muted">VIX</div>
              <div className={`text-lg font-mono font-bold ${parseFloat(vix) > 25 ? 'text-loss' : parseFloat(vix) > 18 ? 'text-warning' : 'text-profit'}`}>
                {parseFloat(vix).toFixed(1)}
              </div>
              <div className="text-[10px] text-text-muted">
                {parseFloat(vix) > 25 ? 'High Vol' : parseFloat(vix) > 18 ? 'Elevated' : 'Low Vol'}
              </div>
            </div>
          )}
          {mentions > 0 && (
            <div className="bg-bg-primary border border-border p-2">
              <div className="text-[10px] uppercase text-text-muted">{latestSocial?.source || 'Social'} Mentions</div>
              <div className="text-lg font-mono font-bold text-text-primary">{mentions.toLocaleString()}</div>
              {(bullPct > 0 || bearPct > 0) && (
                <div className="text-[10px] font-mono">
                  <span className="text-profit">{(bullPct * 100).toFixed(0)}%</span>
                  <span className="text-text-muted"> bull</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {hasNews ? (
        <>
          <div className="text-[10px] uppercase text-text-muted px-1">
            {isCrypto ? 'Crypto FNG' : 'VIX Fear Gauge'} + News
          </div>
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
        </>
      ) : (
        <div className="text-text-muted text-sm py-2">No news sentiment data</div>
      )}
      {latestSocial && (
        <div className="space-y-1.5 px-1">
          <div className="flex items-center gap-2 text-[10px] text-text-secondary">
            <span className="uppercase">{latestSocial.source || 'Social'}</span>
            {mentions > 0 && <span className="text-text-muted">({mentions} mentions)</span>}
          </div>
          {/* bullish/bearish bar */}
          {(bullPct > 0 || bearPct > 0) && (
            <div className="flex h-2 w-full overflow-hidden rounded">
              <div className="bg-profit transition-all" style={{ width: `${bullPct * 100}%` }}
                title={`Bullish: ${(bullPct * 100).toFixed(0)}%`} />
              <div className="bg-loss transition-all" style={{ width: `${bearPct * 100}%` }}
                title={`Bearish: ${(bearPct * 100).toFixed(0)}%`} />
              <div className="bg-bg-primary flex-1" />
            </div>
          )}
          <div className="flex gap-3 text-xs">
            {latestSocial.raw_score != null ? (
              <span className={`font-mono ${parseFloat(latestSocial.raw_score) >= 0 ? 'text-profit' : 'text-loss'}`}>
                {parseFloat(latestSocial.raw_score).toFixed(2)}
              </span>
            ) : (
              <>
                <span className="text-profit font-mono">{(bullPct * 100).toFixed(0)}% bull</span>
                <span className="text-loss font-mono">{(bearPct * 100).toFixed(0)}% bear</span>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// / quant metrics: strategy performance for this symbol
function QuantMetricsPanel({ symbol }) {
  const { data, loading } = useApi(`/api/quant-metrics/${symbol}`, 60000)

  if (loading && !data) return <div className="text-text-muted text-sm py-2">Loading...</div>

  if (!data || data.length === 0) {
    return <div className="text-text-muted text-sm py-2">No quant metrics yet — strategies need live/paper trades</div>
  }

  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-text-secondary text-[11px] uppercase">
          <th className="text-left px-2 py-1">Strategy</th>
          <th className="text-right px-2 py-1">Sharpe</th>
          <th className="text-right px-2 py-1">Sortino</th>
          <th className="text-right px-2 py-1">Max DD</th>
          <th className="text-right px-2 py-1">Win Rate</th>
          <th className="text-right px-2 py-1">Brier</th>
          <th className="text-right px-2 py-1">Score</th>
        </tr>
      </thead>
      <tbody>
        {data.map((s, i) => {
          const sharpe = parseFloat(s.sharpe_ratio || 0)
          const sortino = parseFloat(s.sortino_ratio || 0)
          const dd = parseFloat(s.max_drawdown || 0)
          const wr = parseFloat(s.win_rate || 0)
          const brier = parseFloat(s.brier_score || 0)
          const score = parseFloat(s.composite_score || 0)
          return (
            <tr key={i} className="border-t border-border" style={{ height: 32 }}>
              <td className="px-2 py-1 font-mono truncate max-w-[120px]" title={s.strategy_id}>
                {s.strategy_id}
              </td>
              <td className={`px-2 py-1 text-right font-mono ${sharpe >= 1 ? 'text-profit' : sharpe < 0 ? 'text-loss' : ''}`}>
                {sharpe.toFixed(2)}
              </td>
              <td className={`px-2 py-1 text-right font-mono ${sortino >= 1 ? 'text-profit' : sortino < 0 ? 'text-loss' : ''}`}>
                {sortino.toFixed(2)}
              </td>
              <td className={`px-2 py-1 text-right font-mono ${dd > -0.1 ? 'text-profit' : 'text-loss'}`}>
                {(dd * 100).toFixed(1)}%
              </td>
              <td className={`px-2 py-1 text-right font-mono ${wr >= 0.5 ? 'text-profit' : 'text-loss'}`}>
                {(wr * 100).toFixed(0)}%
              </td>
              <td className={`px-2 py-1 text-right font-mono ${brier < 0.2 ? 'text-profit' : 'text-warning'}`}>
                {brier.toFixed(3)}
              </td>
              <td className={`px-2 py-1 text-right font-mono font-semibold ${scoreColor(score)}`}>
                {score.toFixed(1)}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
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

      {/* row 3: indicators + fundamentals + dcf */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        <Panel title="Technical Indicators">
          <IndicatorsPanel symbol={symbol} />
        </Panel>
        <Panel title="Fundamentals">
          <FundamentalsPanel fundamentals={d.fundamentals} score={d.score} />
        </Panel>
        <Panel title="DCF Valuation">
          <DcfPanel dcf={d.dcf} />
        </Panel>
      </div>

      {/* row 4: sentiment + insider */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <Panel title="Sentiment">
          <SentimentPanel sentiment={d.sentiment} socialSentiment={d.social_sentiment}
            isCrypto={symbol.includes('-USD') || symbol.includes('/')} score={d.score} />
        </Panel>
        <Panel title="Insider Activity">
          <InsiderPanel insiderTrades={d.insider_trades} score={d.score} symbol={symbol} />
        </Panel>
      </div>

      {/* row 5: ai analysis — stacked vertical */}
      <Panel title="AI Analysis">
        <AiAnalysisPanel score={d.score} />
      </Panel>

      {/* row 6: quant metrics */}
      <Panel title="Quant Metrics">
        <QuantMetricsPanel symbol={symbol} />
      </Panel>

      {/* row 7: evolution history */}
      <Panel title="Evolution History">
        <EvolutionPanel evolution={d.evolution} />
      </Panel>

      {/* row 8: trades + signals */}
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
      <Panel title="Strategy Evaluation">
        <StrategyEvalPanel onSelect={setSelectedSymbol} />
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
