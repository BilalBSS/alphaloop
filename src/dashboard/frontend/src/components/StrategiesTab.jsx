import { useMemo } from 'react'
import SectionH from './ui/SectionH'
import Card from './ui/Card'
import Pill from './ui/Pill'
import Sparkline from './ui/Sparkline'

// / composite-ranked strategy rows

const STATUS_TO_PILL = {
  promoted: 'live',
  live: 'live',
  active: 'live',
  paper: 'paper',
  paper_trading: 'paper',
  killed: 'killed',
  failed: 'killed',
  retired: 'killed',
  testing: 'paper',
}

function statusVariant(status) {
  const k = (status || '').toLowerCase()
  return STATUS_TO_PILL[k] || ''
}

function compositeTone(c) {
  if (c == null) return ''
  if (c >= 70) return 'pos'
  if (c >= 40) return 'warn'
  return 'neg'
}

function fmtNum(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—'
  return Number(n).toFixed(digits)
}

function fmtPct(n, digits = 1) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—'
  return `${(Number(n) * 100).toFixed(digits)}%`
}

function fmtDD(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—'
  return `${(Number(n) * 100).toFixed(1)}%`
}

// / pseudo per-strategy equity
function fakeEquitySeries(strategy) {
  const sharpe = Number(strategy.sharpe_ratio ?? 0)
  const win = Number(strategy.win_rate ?? 0.5)
  const seed = (strategy.strategy_id || '').split('').reduce((s, c) => s + c.charCodeAt(0), 0) || 1
  const points = 24
  const out = []
  let v = 1.0
  for (let i = 0; i < points; i++) {
    const noise = Math.sin((seed + i * 13) * 0.7) * 0.5 + Math.cos((seed + i * 5) * 0.3) * 0.5
    const drift = (sharpe * 0.001) + (win - 0.5) * 0.002
    v = v * (1 + drift + noise * 0.01)
    out.push(v)
  }
  return out
}

function StrategyRow({ strategy }) {
  const id = strategy.strategy_id
  const desc = strategy.description ?? strategy.name ?? ''
  const composite = Number(strategy.composite_score ?? 0)
  const sharpe = strategy.sharpe_ratio
  const win = strategy.win_rate
  const dd = strategy.max_drawdown
  const trades = strategy.fills_count ?? strategy.trades_count ?? strategy.total_trades ?? 0
  const gen = strategy.generation
  const tone = compositeTone(composite)
  const sparkData = useMemo(() => fakeEquitySeries(strategy), [strategy])

  return (
    <div className="strat-r">
      <div className="cspark">
        <Sparkline data={sparkData} width={24} height={18} tone={tone === 'pos' ? 'pos' : tone === 'neg' ? 'neg' : 'acc'} />
      </div>
      <div className="nm">
        {id}
        {desc && <div className="d">{desc}</div>}
      </div>
      <div className="num">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 8 }}>
          <div style={{ flex: 1, maxWidth: 70, height: 4, background: 'var(--bg-3)', border: '1px solid var(--line)', position: 'relative' }}>
            <div
              style={{
                position: 'absolute',
                inset: 0,
                width: `${Math.max(0, Math.min(100, composite))}%`,
                background: tone === 'pos' ? 'var(--pos)' : tone === 'warn' ? 'var(--warn)' : tone === 'neg' ? 'var(--neg)' : 'var(--ink-3)',
              }}
            />
          </div>
          <span className={tone}>{fmtNum(composite, 1)}</span>
        </div>
      </div>
      <div className="num">{fmtNum(sharpe, 2)}</div>
      <div className="num">{fmtPct(win, 0)}</div>
      <div className={`num ${dd != null && Number(dd) < -0.2 ? 'neg' : ''}`}>{fmtDD(dd)}</div>
      <div className="num">
        {trades}{gen != null ? <span className="dim"> · g{gen}</span> : null}
      </div>
      <div className="num">
        <Pill variant={statusVariant(strategy.status)}>{strategy.status || 'unknown'}</Pill>
      </div>
    </div>
  )
}

export default function StrategiesTab({ strategies }) {
  const ranked = useMemo(() => {
    if (!Array.isArray(strategies)) return []
    return [...strategies].sort((a, b) => Number(b.composite_score ?? 0) - Number(a.composite_score ?? 0))
  }, [strategies])

  const promotedCount = ranked.filter((s) => s.status === 'promoted' || s.status === 'live' || s.status === 'active').length
  const paperCount = ranked.filter((s) => s.status === 'paper_trading' || s.status === 'paper' || s.status === 'testing').length

  return (
    <SectionH
      num="03"
      title="strategy pool"
      em="composite-ranked"
      meta={<><code>/api/strategies</code> · {promotedCount} promoted · {paperCount} paper · sorted by composite ↓</>}
    >
      <p className="p" style={{ marginBottom: 14 }}>
        Composite weights <code>0.55·sharpe + 0.25·win − 0.30·|dd| − 0.20·brier</code> rank the pool. Promotion gate: <code>Sharpe ≥ 0.8 · win rate ≥ 45% · ≥ 7 days paper</code>. Underperformers killed after ≥ 7 days &amp; ≥ 10 trades.
      </p>

      <Card>
        <div className="strat-r head">
          <div></div>
          <div>strategy</div>
          <div className="num">composite</div>
          <div className="num">sharpe</div>
          <div className="num">win</div>
          <div className="num">drawdown</div>
          <div className="num">trades · gen</div>
          <div className="num">status</div>
        </div>
        {ranked.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-title">no strategies loaded</div>
            <div className="empty-state-hint">configs/strategies/*.json load on next scheduler tick.</div>
          </div>
        ) : (
          ranked.map((s) => <StrategyRow key={s.strategy_id} strategy={s} />)
        )}
      </Card>
    </SectionH>
  )
}
