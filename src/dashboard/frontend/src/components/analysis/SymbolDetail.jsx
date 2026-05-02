import { useState, useEffect } from 'react'
import { useApi } from '../../hooks/useApi'
import { useWebSocketContext } from '../../contexts/WebSocketContext'
import Card from '../ui/Card'
import Pill from '../ui/Pill'
import { SkeletonChart } from '../Skeleton'
import { fmtLargeNum, fmtCount } from './formatters'
import LWChart, { DEFAULT_MARKER_KINDS } from '../chart/LWChart'
import TimeSeriesLineChart from '../chart/TimeSeriesLineChart'
import IndicatorPicker from '../chart/tools/IndicatorPicker'
import DrawingToolbar from '../chart/tools/DrawingToolbar'
import ReplaySlider from '../chart/tools/ReplaySlider'
import CompareBar from '../chart/tools/CompareBar'
import VolumeProfileToggle from '../chart/tools/VolumeProfileToggle'
import ChartErrorBoundary from '../chart/ChartErrorBoundary'
import { useChartState } from '../chart/useChartState'
import { ChartToolsProvider, useChartTools } from '../chart/useDrawings'

// / extracted sub-cards
import ScoreGrid from './ScoreGrid'
import DualLLMColumn from './DualLLMColumn'
import IndicatorsCard from './IndicatorsCard'
import InsiderCard from './InsiderCard'
import PositionsCard from './PositionsCard'
import ICTCard from './ICTCard'
import QuantMetricsCard from './QuantMetricsCard'
import SentimentCard from './SentimentCard'

import {
  MacroRegimeCard,
  AnalystConsensusCard,
  OptionsFlowCard,
  DarkPoolCard,
  CongressionalCard,
  ShortSqueezeCard,
  EarningsRevisionsCard,
} from './AltDataPanels'
import CryptoFundamentalsCard from './CryptoFundamentalsCard'

// / daily price chart
function PriceChart({ priceHistory }) {
  if (!priceHistory || priceHistory.length === 0) {
    return <div className="dim" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 220, fontSize: 12 }}>No price data</div>
  }
  return (
    <TimeSeriesLineChart
      data={priceHistory}
      timeKey="date"
      valueKey="close"
      color="var(--acc)"
      height={220}
      valueFmt={v => `$${v.toFixed(2)}`}
      emptyText="No price data"
    />
  )
}

// / timeframe toggle
function TimeframeToggle({ tf, setTf }) {
  return (
    <div style={{ display: 'flex', gap: 6 }}>
      {['daily', '2h'].map(t => {
        const active = tf === t
        return (
          <button
            key={t}
            type="button"
            onClick={() => setTf(t)}
            className="btn"
            style={{
              fontSize: 10.5,
              padding: '3px 9px',
              color: active ? 'var(--acc)' : undefined,
              borderColor: active ? 'var(--acc)' : undefined,
              background: active ? 'var(--bg-3)' : undefined,
            }}
          >
            {t}
          </button>
        )
      })}
    </div>
  )
}

// / drawing toolbar bridge
function DrawingToolbarBridge() {
  const tools = useChartTools()
  if (!tools) return null
  return (
    <DrawingToolbar
      activeTool={tools.activeTool}
      setTool={tools.setTool}
      clear={tools.clear}
      undo={tools.undo}
    />
  )
}

// / price card + toolbar
function PriceCard({ symbol, priceHistory, tf, setTf }) {
  const { state, toggleIndicator } = useChartState(symbol)
  const showPicker = tf === '2h'
  const markerKinds = (state.indicator_params && Array.isArray(state.indicator_params.marker_kinds))
    ? state.indicator_params.marker_kinds
    : DEFAULT_MARKER_KINDS

  const [replayEnabled, setReplayEnabled] = useState(false)
  const [replayCutoff, setReplayCutoff] = useState(null)
  const [replayWindow, setReplayWindow] = useState({ minT: null, maxT: null })
  const [compareAgainst, setCompareAgainst] = useState('')
  const [compareEnabled, setCompareEnabled] = useState(false)
  const [volumeProfileEnabled, setVolumeProfileEnabled] = useState(false)

  const handleToggleReplay = () => {
    if (replayEnabled) {
      setReplayEnabled(false)
      setReplayCutoff(null)
      setReplayWindow({ minT: null, maxT: null })
      return
    }
    const now = Date.now()
    const minT = new Date(now - 30 * 24 * 3600 * 1000).toISOString()
    const maxT = new Date(now).toISOString()
    setReplayWindow({ minT, maxT })
    setReplayCutoff(maxT)
    setReplayEnabled(true)
  }

  const cardTitle = (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 12 }}>
      <span><b>price</b> · {tf} · 60 sessions</span>
      <TimeframeToggle tf={tf} setTf={setTf} />
    </span>
  )

  const cardMeta = showPicker ? (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <ReplaySlider
        enabled={replayEnabled}
        onToggle={handleToggleReplay}
        onCutoffChange={setReplayCutoff}
        minT={replayWindow.minT}
        maxT={replayWindow.maxT}
        cutoff={replayCutoff}
      />
      <CompareBar
        base={symbol}
        against={compareAgainst}
        setAgainst={setCompareAgainst}
        enabled={compareEnabled}
        setEnabled={setCompareEnabled}
      />
      <VolumeProfileToggle
        enabled={volumeProfileEnabled}
        setEnabled={setVolumeProfileEnabled}
      />
      <DrawingToolbarBridge />
      <IndicatorPicker selected={state.active_indicators} onToggle={toggleIndicator} />
    </span>
  ) : null

  return (
    <ChartToolsProvider>
      <Card title={cardTitle} meta={cardMeta}>
        {tf === 'daily' ? (
          <PriceChart priceHistory={priceHistory} />
        ) : (
          <ChartErrorBoundary>
            <LWChart
              symbol={symbol}
              indicators={state.active_indicators}
              markerKinds={markerKinds}
              replayEnabled={replayEnabled}
              replayCutoff={replayCutoff}
              compareAgainst={compareAgainst}
              compareEnabled={compareEnabled}
              volumeProfileEnabled={volumeProfileEnabled}
            />
          </ChartErrorBoundary>
        )}
      </Card>
    </ChartToolsProvider>
  )
}

// / fundamentals + edgar footer
function FundamentalsPanel({ fundamentals, score }) {
  if (!fundamentals) return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No fundamentals data</div>

  const rows = [
    { label: 'P/E', val: fundamentals.pe_ratio, sector: fundamentals.sector_pe_avg, lower: true },
    { label: 'P/S', val: fundamentals.ps_ratio, sector: fundamentals.sector_ps_avg, lower: true },
    { label: 'PEG', val: fundamentals.peg_ratio, sector: null, lower: true },
    { label: 'FCF margin', val: fundamentals.fcf_margin, sector: fundamentals.sector_fcf_margin_avg, lower: false, pct: true },
    { label: 'D/E', val: fundamentals.debt_to_equity, sector: fundamentals.sector_de_avg, lower: true },
    { label: 'Rev growth 1Y', val: fundamentals.revenue_growth_1y, sector: fundamentals.sector_rev_growth_avg, lower: false, pct: true },
  ]

  const allZeroOrNull = rows.every(r => r.val == null || parseFloat(r.val) === 0)
  if (allZeroOrNull) {
    return <div className="dim" style={{ padding: 14, fontSize: 12 }}>Not applicable for this asset type</div>
  }

  const details = (score?.details && typeof score.details === 'object') ? score.details : {}
  const src = fundamentals || {}
  const dataSource = src.data_source || details.data_source
  const edgarRows = [
    { label: 'revenue', val: src.total_revenue || src.revenue || details.revenue },
    { label: 'net income', val: src.net_income || details.net_income },
    { label: 'free cash flow', val: src.free_cash_flow || details.free_cash_flow },
    { label: 'total cash', val: src.total_cash || details.total_cash },
    { label: 'total debt', val: src.total_debt || details.total_debt },
    { label: 'shares outstanding', val: src.shares_outstanding || details.shares_outstanding, isCount: true },
  ].filter(r => r.val != null)

  return (
    <div>
      <table className="tbl">
        <thead>
          <tr>
            <th>metric</th>
            <th className="r">value</th>
            <th className="r">sector</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const v = parseFloat(r.val || 0)
            const s = r.sector ? parseFloat(r.sector) : null
            const better = s !== null ? (r.lower ? v < s : v > s) : null
            const tone = better === true ? 'pos' : better === false ? 'warn' : ''
            return (
              <tr key={r.label}>
                <td>{r.label}</td>
                <td className={`r ${tone}`}>{r.pct ? `${(v * 100).toFixed(1)}%` : v.toFixed(2)}</td>
                <td className="r dim">{s !== null ? (r.pct ? `${(s * 100).toFixed(1)}%` : s.toFixed(2)) : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {edgarRows.length > 0 && (
        <div style={{ padding: '10px 14px', borderTop: '1px solid var(--line)', fontSize: 10.5, color: 'var(--ink-3)', fontFamily: 'var(--mono)', lineHeight: 1.7 }}>
          {edgarRows.map((r, i) => (
            <span key={r.label}>
              {i > 0 && ' · '}
              {r.label} <span style={{ color: 'var(--ink-2)' }}>{r.isCount ? fmtCount(r.val) : fmtLargeNum(r.val)}</span>
            </span>
          ))}
          {dataSource && <div style={{ marginTop: 4 }}>data source: <code>{dataSource}</code></div>}
        </div>
      )}
    </div>
  )
}

// / dcf valuation bar
function DcfPanel({ dcf }) {
  if (!dcf || !dcf.fair_value_median) return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No DCF data</div>

  const median = parseFloat(dcf.fair_value_median || 0)
  const current = parseFloat(dcf.current_price || 0)

  if (median === 0 || current === 0) {
    return <div className="dim" style={{ padding: 14, fontSize: 12 }}>Insufficient data for DCF valuation</div>
  }

  const p10 = parseFloat(dcf.fair_value_p10 || 0)
  const p90 = parseFloat(dcf.fair_value_p90 || 0)
  const upside = parseFloat(dcf.upside_pct || 0)

  const range = p90 - p10
  const medianPct = range > 0 ? ((median - p10) / range) * 100 : 50
  const currentPct = range > 0 ? Math.min(100, Math.max(0, ((current - p10) / range) * 100)) : 50
  const upsideTone = upside >= 0 ? 'pos' : 'neg'

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--ink-3)', marginBottom: 6 }}>
        <span>P10 · ${p10.toFixed(0)}</span>
        <span style={{ color: 'var(--ink)' }}><b>median · ${median.toFixed(0)}</b></span>
        <span>P90 · ${p90.toFixed(0)}</span>
      </div>
      <div style={{ position: 'relative', height: 14, background: 'var(--bg)', border: '1px solid var(--line)' }}>
        <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to right, rgba(216,180,102,.05), rgba(216,180,102,.20), rgba(216,180,102,.05))' }} />
        <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${medianPct}%`, width: 2, background: 'var(--acc)' }} title={`median $${median.toFixed(0)}`} />
        <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${currentPct}%`, width: 2, background: 'var(--pos)' }} title={`current $${current.toFixed(0)}`} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 10, fontFamily: 'var(--mono)', fontSize: 11.5 }}>
        <span className="dim">current · <span style={{ color: 'var(--ink)' }}>${current.toFixed(2)}</span></span>
        <span className={upsideTone}>{upside >= 0 ? '+' : ''}{(upside * 100).toFixed(1)}% upside vs median</span>
      </div>
      <p style={{ fontSize: 11, color: 'var(--ink-3)', margin: '14px 0 0', fontFamily: 'var(--mono)', lineHeight: 1.65 }}>
        {dcf.num_simulations || '10k'} Monte Carlo paths · antithetic + control variates ·
        {' '}<span className="dim">confidence {dcf.dcf_confidence || '—'}</span>
      </p>
    </div>
  )
}

// / evolution events table
function EvolutionPanel({ evolution }) {
  if (!evolution || evolution.length === 0) {
    return (
      <div className="dim" style={{ padding: 14, fontSize: 12 }}>
        No evolution events for this symbol yet. Evolution runs nightly at midnight ET.
      </div>
    )
  }
  const actionTone = {
    spawn: 'acc', spawn_tier2: 'acc', mutate: 'acc',
    kill: 'neg', promote: 'pos', graduate: 'pos',
  }
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th>gen</th>
          <th>action</th>
          <th>strategy</th>
          <th className="r">date</th>
        </tr>
      </thead>
      <tbody>
        {evolution.map((e, i) => (
          <tr key={i}>
            <td>{e.generation}</td>
            <td className={actionTone[e.action] || 'dim'}>{(e.action || '').toUpperCase()}</td>
            <td className="dim tiny" title={e.strategy_id}>{e.strategy_id}</td>
            <td className="r dim">{e.created_at?.split('T')[0] || '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// / trade history table
function TradeHistoryPanel({ trades }) {
  if (!trades || trades.length === 0) {
    return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No trades for this symbol</div>
  }
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th>side</th>
          <th className="r">qty</th>
          <th className="r">price</th>
          <th className="r">P&amp;L</th>
          <th className="r">date</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t, i) => {
          const pnl = parseFloat(t.pnl || 0)
          const sideTone = t.side === 'buy' ? 'pos' : 'neg'
          const pnlTone = pnl >= 0 ? 'pos' : 'neg'
          return (
            <tr key={i}>
              <td className={sideTone}>{(t.side || '').toUpperCase()}</td>
              <td className="r">{t.qty}</td>
              <td className="r">${parseFloat(t.price || 0).toFixed(2)}</td>
              <td className={`r ${pnlTone}`}>{pnl !== 0 ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}` : '—'}</td>
              <td className="r dim">{t.created_at?.split('T')[0] || '—'}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// / signals + strategy pills
function SignalsPanel({ signals }) {
  if (!signals || signals.length === 0) {
    return <div className="dim" style={{ padding: 14, fontSize: 12 }}>No signals for this symbol</div>
  }

  const breakdown = (() => {
    const map = {}
    for (const s of signals) {
      const key = s.strategy_id || 'unknown'
      if (!map[key]) map[key] = { buys: 0, sells: 0 }
      if (s.signal_type === 'buy') map[key].buys++
      else map[key].sells++
    }
    return Object.entries(map).map(([id, v]) => ({ id, ...v }))
  })()

  return (
    <div>
      {breakdown.length > 0 && (
        <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--line)', fontSize: 11.5 }}>
          <div style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>strategies active</div>
          <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {breakdown.map(b => (
              <Pill key={b.id} variant="strat">
                {b.id} · {b.buys}B{b.sells > 0 ? ` ${b.sells}S` : ''}
              </Pill>
            ))}
          </div>
        </div>
      )}
      <table className="tbl">
        <thead>
          <tr>
            <th>type</th>
            <th className="r">strength</th>
            <th>strategy</th>
            <th className="r">date</th>
          </tr>
        </thead>
        <tbody>
          {signals.slice(0, 12).map((s, i) => {
            const tone = s.signal_type === 'buy' ? 'pos' : 'neg'
            return (
              <tr key={i}>
                <td className={tone}>{(s.signal_type || '').toUpperCase()}</td>
                <td className="r">{parseFloat(s.strength || 0).toFixed(2)}</td>
                <td className="dim tiny">{s.strategy_id}</td>
                <td className="r dim">{s.created_at?.split('T')[0] || '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// / sym-detail header pills
function regimeVariant(r) {
  if (r === 'bull') return 'bull'
  if (r === 'bear') return 'bear'
  if (r === 'sideways' || r === 'high_vol') return 'sideways'
  return 'regime'
}

function consensusVariant(c) {
  if (c === 'bullish') return 'bullish'
  if (c === 'bearish') return 'bearish'
  return 'neutral'
}

// / per-symbol detail view
export default function SymbolDetail({ symbol, onBack }) {
  const [tf, setTf] = useState('daily')
  const { data, loading, error, refetch } = useApi(`/api/analysis/${symbol}`, 30000)
  const { data: stratPositions } = useApi(`/api/strategy-positions?symbol=${symbol}`, 60000)
  const { subscribe } = useWebSocketContext()
  const isCrypto = symbol.includes('-USD') || symbol.includes('/')

  // / refetch on signal_generated
  useEffect(() => {
    const unsub = subscribe('signal_generated', (msg) => {
      const d = msg?.data || msg
      if (!d) return
      if (d.symbol && d.symbol === symbol) refetch()
    })
    return unsub
  }, [subscribe, symbol, refetch])

  if (loading && !data) {
    return (
      <div style={{ display: 'grid', gap: 12 }}>
        <button onClick={onBack} className="btn" style={{ width: 'fit-content' }}>← back to list</button>
        <SkeletonChart />
        <SkeletonChart />
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ display: 'grid', gap: 12 }}>
        <button onClick={onBack} className="btn" style={{ width: 'fit-content' }}>← back to list</button>
        <Card title={symbol}>
          <div className="neg" style={{ padding: 14 }}>Failed to load: {error}</div>
        </Card>
      </div>
    )
  }

  const d = data || {}
  const regime = d.score?.regime
  const consensus = (typeof d.score?.details === 'object' ? d.score.details.ai_consensus : null) || d.score?.ai_consensus
  const heldStrats = Array.isArray(stratPositions) ? stratPositions.filter(p => p.strategy_id && p.strategy_id.toLowerCase() !== 'untracked') : []
  const heldCount = heldStrats.length

  return (
    <div style={{ display: 'grid', gap: 18 }}>
      <button onClick={onBack} className="btn" style={{ width: 'fit-content' }}>← back to list</button>

      <div className="sym-detail-h">
        <div className="ttl">
          {symbol}
          {isCrypto && <span className="meta">crypto</span>}
        </div>
        <div className="right">
          {regime && <Pill variant={regimeVariant(regime)}>regime · {regime}</Pill>}
          {consensus && <Pill variant={consensusVariant(consensus)}>consensus · {consensus}</Pill>}
          {heldCount > 0 && <Pill variant="live">held · {heldCount} strat</Pill>}
        </div>
      </div>

      {/* score grid + composite breakdown */}
      <ScoreGrid score={d.score} />

      {/* macro regime context */}
      <Card title={<><b>macro regime</b></>} meta="fed funds · cpi · unemployment · yield spread">
        <MacroRegimeCard />
      </Card>

      {/* price + indicators */}
      <div className="grid c12-7-5">
        <PriceCard symbol={symbol} priceHistory={d.price_history} tf={tf} setTf={setTf} />
        <Card title={<><b>indicators</b> · {tf}</>} meta={<code>/api/indicators/{symbol}</code>} p0>
          <IndicatorsCard symbol={symbol} tf={tf} />
        </Card>
      </div>

      {/* fundamentals + DCF */}
      <div className="grid c2">
        <Card title={<><b>fundamentals</b></>} meta={isCrypto ? 'crypto metrics' : <code>edgar</code>} p0>
          {isCrypto
            ? <CryptoFundamentalsCard symbol={symbol} />
            : <FundamentalsPanel fundamentals={d.fundamentals} score={d.score} />}
        </Card>
        <Card title={<><b>DCF valuation</b> · 10k MC sims</>} meta={d.dcf?.dcf_confidence ? <>confidence <span className="pos">{d.dcf.dcf_confidence}</span></> : null}>
          <DcfPanel dcf={d.dcf} />
        </Card>
      </div>

      {/* alt data row 1: analyst · options · dark pool (stocks only) */}
      {!isCrypto && (
        <div className="grid c3">
          <Card title={<><b>analyst consensus</b></>} meta={<code>/api/analyst-ratings/{symbol}</code>}>
            <AnalystConsensusCard symbol={symbol} />
          </Card>
          <Card title={<><b>options flow</b> · 24h</>} meta={<code>/api/options/{symbol}</code>}>
            <OptionsFlowCard symbol={symbol} />
          </Card>
          <Card title={<><b>dark pool</b> · 24h</>} meta={<code>/api/dark-pool/{symbol}</code>}>
            <DarkPoolCard symbol={symbol} />
          </Card>
        </div>
      )}

      {/* alt data row 2: congressional · short squeeze · earnings revisions (stocks only) */}
      {!isCrypto && (
        <div className="grid c3">
          <Card title={<><b>congressional activity</b></>}>
            <CongressionalCard symbol={symbol} />
          </Card>
          <Card title={<><b>short squeeze risk</b></>}>
            <ShortSqueezeCard symbol={symbol} />
          </Card>
          <Card title={<><b>earnings revisions</b></>}>
            <EarningsRevisionsCard symbol={symbol} />
          </Card>
        </div>
      )}

      {/* insider + sentiment row */}
      <div className="grid c2">
        <Card title={<><b>insider activity</b> · last 90d</>} meta={<code>/api/insider/{symbol}</code>} p0>
          <InsiderCard insiderTrades={d.insider_trades} score={d.score} symbol={symbol} />
        </Card>
        <Card title={<><b>sentiment</b></>} meta="vix · social · news">
          <SentimentCard
            sentiment={d.sentiment}
            socialSentiment={d.social_sentiment}
            isCrypto={isCrypto}
            score={d.score}
          />
        </Card>
      </div>

      {/* dual-LLM (renders its own .grid.c2 wrapper) */}
      <DualLLMColumn score={d.score} />

      {/* signals + ICT */}
      <div className="grid c12-7-5">
        <Card title={<><b>signals</b> · this symbol · 14d</>} meta={<code>/api/signals/{symbol}</code>} p0>
          <SignalsPanel signals={d.signals} />
        </Card>
        <Card title={<><b>ICT levels</b></>} meta={<code>/api/ict-indicators/{symbol}</code>} p0>
          <ICTCard symbol={symbol} />
        </Card>
      </div>

      {/* quant metrics + evolution */}
      <div className="grid c12-7-5">
        <Card title={<><b>quant metrics</b> · per strategy</>} meta={<code>/api/quant-metrics/{symbol}</code>} p0>
          <QuantMetricsCard symbol={symbol} />
        </Card>
        <Card title={<><b>evolution events</b></>} meta="this symbol" p0>
          <EvolutionPanel evolution={d.evolution} />
        </Card>
      </div>

      {/* positions + trade history */}
      <div className="grid c2">
        <Card title={<><b>open positions</b></>} meta={<code>/api/strategy-positions</code>} p0>
          <PositionsCard symbol={symbol} />
        </Card>
        <Card title={<><b>trade history</b></>} p0>
          <TradeHistoryPanel trades={d.trades} />
        </Card>
      </div>
    </div>
  )
}
