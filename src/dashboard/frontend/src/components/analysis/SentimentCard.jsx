import NewsSentimentLWChart from '../chart/NewsSentimentLWChart'

// / vix social news
function fngLabel(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  if (n <= 20) return 'extreme fear'
  if (n <= 40) return 'fear'
  if (n <= 60) return 'neutral'
  if (n <= 80) return 'greed'
  return 'extreme greed'
}

function fngTone(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return ''
  if (n <= 30) return 'neg'
  if (n <= 60) return 'warn'
  return 'pos'
}

function vixTone(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return ''
  if (n > 25) return 'neg'
  if (n > 18) return 'warn'
  return 'pos'
}

function vixLabel(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  if (n > 30) return 'extreme fear'
  if (n > 25) return 'high vol'
  if (n > 18) return 'elevated'
  if (n > 12) return 'low vol'
  return 'complacent'
}

export default function SentimentCard({ sentiment, socialSentiment, isCrypto, score }) {
  const details = (score?.details && typeof score.details === 'object') ? score.details : {}
  const apewisdom = socialSentiment?.find(s => s.source === 'apewisdom') || null
  const latestSocial = apewisdom || (Array.isArray(socialSentiment) && socialSentiment.length > 0 ? socialSentiment[0] : null)

  const mentions = latestSocial ? parseInt(latestSocial.volume || 0) : 0
  const fng = details.fear_greed_index || details.fear_greed || latestSocial?.raw_score
  const vix = details.vix || details.vix_level
  const hasNews = Array.isArray(sentiment) && sentiment.length > 0

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div className="grid c3">
        <div className="tile">
          <div className="lab">{isCrypto ? 'crypto fear & greed' : 'vix · fear gauge'}</div>
          {isCrypto ? (
            <>
              <div className={`v ${fngTone(fng)}`}>{fng != null ? parseFloat(fng).toFixed(0) : '—'}</div>
              <div className="x">{fng != null ? fngLabel(fng) : 'no data'}</div>
            </>
          ) : (
            <>
              <div className={`v ${vixTone(vix)}`}>{vix != null ? parseFloat(vix).toFixed(1) : '—'}</div>
              <div className="x">{vix != null ? vixLabel(vix) : 'no data'} · &lt;15 calm, &gt;25 fear</div>
            </>
          )}
        </div>
        <div className="tile">
          <div className="lab">social · apewisdom</div>
          <div className="v">{mentions > 0 ? mentions.toLocaleString() : '—'}</div>
          <div className="x">
            {mentions > 0 ? 'reddit mentions 24h' : 'not trending'}
            {apewisdom?.raw_score != null && (
              <> · score <span className={parseFloat(apewisdom.raw_score) >= 0 ? 'pos' : 'neg'}>
                {parseFloat(apewisdom.raw_score).toFixed(2)}
              </span></>
            )}
          </div>
        </div>
        <div className="tile">
          <div className="lab">news sentiment</div>
          <div className="v">{hasNews ? `${sentiment.length} pts` : '—'}</div>
          <div className="x">{hasNews ? '7d trend below' : 'no recent news'}</div>
        </div>
      </div>
      {hasNews && (
        <div>
          <div style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 6 }}>
            news sentiment · 7d
          </div>
          <NewsSentimentLWChart data={sentiment} height={140} />
        </div>
      )}
    </div>
  )
}
