// / 4-cell composite grid

const COMPONENTS = [
  { key: 'ratio_score_100', label: 'Ratio', weight: 35, rgb: '216,180,102' },
  { key: 'dcf_score_100', label: 'DCF', weight: 25, rgb: '127,184,122' },
  { key: 'earnings_score_100', label: 'Earnings', weight: 20, rgb: '199,154,58' },
  { key: 'insider_score_100', label: 'Insider', weight: 20, rgb: '111,168,201' },
]

function tone(v) {
  if (v == null) return ''
  if (v >= 65) return 'pos'
  if (v >= 50) return 'warn'
  return 'neg'
}

export default function ScoreGrid({ score }) {
  if (!score) {
    return <div className="dim" style={{ padding: '12px 0', fontSize: 12 }}>no analysis data</div>
  }
  const details = typeof score.details === 'object' ? score.details : {}

  const composite = parseFloat(score.composite_score || 0)
  const fundamental = parseFloat(score.fundamental_score || 0)
  const kronosProb = details.kronos_probability
  const kronosConf = details.kronos_confidence
  const kronosSource = details.kronos_source
  const consensus = details.ai_consensus || score.ai_consensus

  const compTone = tone(composite)
  const fundTone = tone(fundamental)

  const kronosPct = kronosProb != null ? Math.round(parseFloat(kronosProb) * 100) : null
  const kronosTone = kronosPct == null ? '' : kronosPct >= 60 ? 'pos' : kronosPct <= 40 ? 'neg' : 'warn'
  const sourceLabel = kronosSource === 'kronos_hf' ? 'hf-local' : kronosSource === 'fallback_heuristic' ? 'fallback' : kronosSource || '—'
  const sourceCls = kronosSource === 'kronos_hf' ? 'pos' : kronosSource === 'fallback_heuristic' ? 'warn' : 'dim'

  const consensusTone = consensus === 'bullish' ? 'pos' : consensus === 'bearish' ? 'neg' : ''

  const hasBreakdown = COMPONENTS.some(c => details[c.key] != null)

  return (
    <div>
      <div className="score-grid">
        <div className="c">
          <div className="lab">composite</div>
          <div className={`v ${compTone}`}>{composite.toFixed(1)}</div>
        </div>
        <div className="c">
          <div className="lab">fundamental</div>
          <div className={`v ${fundTone}`}>{fundamental.toFixed(1)}</div>
          <div className="x">ratio + dcf + earnings + insider</div>
        </div>
        <div className="c">
          <div className="lab">kronos P(↑)</div>
          <div className={`v ${kronosTone}`}>{kronosPct == null ? '—' : `${kronosPct}%`}</div>
          {kronosPct != null && (
            <div className="x">
              conf {kronosConf != null ? `${(parseFloat(kronosConf) * 100).toFixed(0)}%` : '—'} · src <span className={sourceCls}>{sourceLabel}</span>
            </div>
          )}
        </div>
        <div className="c">
          <div className="lab">analyst consensus</div>
          <div className={`v ${consensusTone}`}>{consensus || '—'}</div>
        </div>
      </div>
      {hasBreakdown && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 6 }}>
            composite breakdown · weight × score
          </div>
          <div className="compbar">
            {COMPONENTS.map(c => {
              const raw = parseFloat(details[c.key] || 0)
              const opacity = Math.max(0.15, Math.min(1, raw / 100)).toFixed(2)
              return (
                <div
                  key={c.key}
                  className="seg"
                  style={{ width: `${c.weight}%`, background: `rgba(${c.rgb},${opacity})` }}
                  title={`${c.label}: ${raw.toFixed(1)} × ${c.weight}%`}
                />
              )
            })}
          </div>
          <div className="compbar-leg">
            {COMPONENTS.map(c => {
              const raw = details[c.key]
              const signed = c.key === 'insider_score_100' ? details.insider_signed_strength : null
              const display = signed != null ? parseFloat(signed) : raw != null ? parseFloat(raw) : null
              const prefix = signed != null && signed > 0 ? '+' : ''
              return (
                <div className="l" key={c.key}>
                  <b>{c.label}</b> {c.weight}% · {display != null ? `${prefix}${display.toFixed(1)}` : '—'}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
