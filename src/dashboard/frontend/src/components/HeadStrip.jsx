import Sparkline from './ui/Sparkline'

// / v3 4-kpi global strip

const fmtMoney = (n) => {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const sign = n < 0 ? '−' : ''
  const abs = Math.abs(Number(n))
  return `${sign}$${abs.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

const fmtPct = (n, digits = 2) => {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const sign = n < 0 ? '−' : '+'
  return `${sign}${Math.abs(Number(n) * 100).toFixed(digits)}%`
}

export default function HeadStrip({ portfolio, equityHistory, strategies, macro, risk }) {
  const nav = portfolio?.equity ?? portfolio?.nav ?? portfolio?.total_value
  const cash = portfolio?.cash
  const buyingPower = portfolio?.buying_power
  const cashPct = cash != null && nav > 0 ? Number(cash) / Number(nav) : null
  const pnl24 = portfolio?.pnl_24h ?? portfolio?.daily_pnl ?? portfolio?.pnl_today
  const pnl24Pct = portfolio?.pnl_24h_pct ?? portfolio?.daily_pnl_pct
  const pnl30d = portfolio?.pnl_30d_pct ?? portfolio?.return_30d
  const pnlAllTime = portfolio?.pnl_all_time_pct ?? portfolio?.return_all_time
  const sharpe = portfolio?.sharpe ?? portfolio?.sharpe_ratio

  const equityPoints =
    equityHistory?.points?.map(p => Number(p.equity ?? p.value)).filter(v => Number.isFinite(v))
    ?? equityHistory?.equity?.map(Number).filter(v => Number.isFinite(v))
    ?? []

  const regime = macro?.regime ?? portfolio?.regime
  const regimeProb = macro?.regime_probability ?? macro?.p_bull ?? portfolio?.regime_confidence
  const regimePrev = macro?.regime_probability_prev ?? macro?.p_bull_prev
  const sizing = macro?.sizing_multiplier ?? macro?.regime_size_multiplier
  const breadth = macro?.breadth_above_50d
  const breadthFlip = macro?.breadth_flip_threshold

  const live = strategies?.filter?.(s => s.status === 'live')?.length ?? strategies?.live_count
  const paper = strategies?.filter?.(s => s.status === 'paper_trading')?.length ?? strategies?.paper_count
  const retired = strategies?.filter?.(s => s.status === 'killed' || s.status === 'retired')?.length ?? strategies?.retired_count
  const total = (live ?? 0) + (paper ?? 0) + (retired ?? 0)
  const generations = strategies?.max_generation ?? strategies?.generations
  const lastMutHours = strategies?.last_mutation_hours_ago

  const riskBudget = risk?.risk_budget ?? risk?.budget_used
  const var95 = risk?.var_95
  const var95Pct = risk?.var_95_pct
  const tailDep = risk?.tail_dependence ?? risk?.tail_lambda
  const drawdown = risk?.current_drawdown ?? portfolio?.drawdown
  const gatesPassing = risk?.gates_passing ?? risk?.gates_pass

  const pnlSign = (pnl24 ?? 0) < 0 ? 'neg' : 'pos'
  const drawdownVal = drawdown !== undefined && drawdown !== null
    ? `${(Number(drawdown) * 100).toFixed(1)}%`
    : '—'
  const regimeTone = regime === 'bull' ? 'pos' : regime === 'bear' ? 'neg' : 'warn'

  return (
    <section className="head">
      <div className="col">
        <div className="lab">net asset value</div>
        <div className="big">{fmtMoney(nav)}</div>
        <div className={`delta ${pnlSign}`}>
          {fmtMoney(pnl24)}
          {pnl24Pct !== undefined && pnl24Pct !== null
            ? ` · ${fmtPct(pnl24Pct)} today`
            : ''}
        </div>
        {equityPoints.length > 1 && (
          <div className="spark">
            <Sparkline data={equityPoints} tone={pnlSign} />
          </div>
        )}
        <div className="sub" style={{ marginTop: '6px' }}>
          {pnl30d !== undefined && pnl30d !== null && <><b>{fmtPct(pnl30d)}</b> 30d · </>}
          {pnlAllTime !== undefined && pnlAllTime !== null && <><b>{fmtPct(pnlAllTime)}</b> since inception</>}
          {sharpe !== undefined && sharpe !== null && <> · sharpe <b>{Number(sharpe).toFixed(2)}</b></>}
        </div>
      </div>

      <div className="col">
        <div className="lab">cash</div>
        <div className="big">{fmtMoney(cash)}</div>
        <div className="sub">
          {cashPct !== null
            ? <><b>{(cashPct * 100).toFixed(1)}%</b> of NAV</>
            : '— of NAV'}
        </div>
        {buyingPower !== undefined && buyingPower !== null && (
          <div className="sub" style={{ marginTop: '8px' }}>
            buying power <b>{fmtMoney(buyingPower)}</b>
          </div>
        )}
      </div>

      <div className="col">
        <div className="lab">regime classifier</div>
        <div className="big" style={{ color: `var(--${regimeTone})` }}>
          {regime ?? '—'}
        </div>
        <div className="sub">
          {regimeProb !== undefined && regimeProb !== null && (
            <><b>P({regime ?? 'bull'}) = {Number(regimeProb).toFixed(2)}</b></>
          )}
          {regimePrev !== undefined && regimePrev !== null && regimeProb !== undefined && (
            <> · {regimeProb >= regimePrev ? '↑' : '↓'} from {Number(regimePrev).toFixed(2)}</>
          )}
        </div>
        {(sizing !== undefined || breadth !== undefined) && (
          <div className="sub" style={{ marginTop: '8px' }}>
            {sizing !== undefined && sizing !== null && <>sizing × <b>{Number(sizing).toFixed(2)}</b></>}
            {breadth !== undefined && breadth !== null && <> · breadth <b>{Math.round(Number(breadth) * 100)}%</b> &gt;50d SMA</>}
          </div>
        )}
        {breadthFlip !== undefined && breadthFlip !== null && (
          <div className="sub" style={{ marginTop: '6px', color: 'var(--ink-4)' }}>
            flips sideways at breadth &lt; {Math.round(Number(breadthFlip) * 100)}%
          </div>
        )}
      </div>

      <div className="col">
        <div className="lab">strategy pool</div>
        <div className="big">{total || '—'}</div>
        <div className="sub">
          <span className="pos">{live ?? 0} live</span>
          {' · '}
          <span className="acc">{paper ?? 0} paper</span>
          {' · '}
          <span className="muted">{retired ?? 0} retired</span>
        </div>
        {generations !== undefined && (
          <div className="sub" style={{ marginTop: '8px' }}>
            <b>{generations}</b> generations
            {lastMutHours !== undefined && lastMutHours !== null && (
              <> · last mut −{Math.round(Number(lastMutHours))}h</>
            )}
          </div>
        )}
      </div>

      <div className="col">
        <div className="lab">risk budget</div>
        <div className="big">
          {riskBudget !== undefined && riskBudget !== null
            ? Number(riskBudget).toFixed(2)
            : '—'}
          <span style={{ fontSize: '18px', color: 'var(--ink-3)', fontWeight: 400 }}> / 1.00</span>
        </div>
        <div className="sub">
          {var95 !== undefined && var95 !== null && <>VaR(95) <b>{fmtMoney(var95)}</b></>}
          {var95Pct !== undefined && var95Pct !== null && <> · {fmtPct(var95Pct, 1)} NAV</>}
        </div>
        <div className="sub" style={{ marginTop: '8px' }}>
          {tailDep !== undefined && tailDep !== null && <>tail-dep λ <b>{Number(tailDep).toFixed(2)}</b></>}
          {drawdown !== undefined && drawdown !== null && <> · drawdown <b className="neg">−{drawdownVal}</b></>}
        </div>
        {gatesPassing !== undefined && gatesPassing !== null && (
          <div className="sub" style={{ marginTop: '6px', color: 'var(--ink-4)' }}>
            {gatesPassing} gates passing
          </div>
        )}
      </div>
    </section>
  )
}
