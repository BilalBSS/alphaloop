import Pill from '../ui/Pill'

// / dual-llm side-by-side

function signalVariant(s) {
  if (s === 'bullish') return 'bullish'
  if (s === 'bearish') return 'bearish'
  return 'neutral'
}

// / inline bold parser
function renderInline(text) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return parts.map((p, i) => {
    if (/^\*\*[^*]+\*\*$/.test(p)) return <b key={i}>{p.slice(2, -2)}</b>
    return <span key={i}>{p}</span>
  })
}

// / detect signal header
function splitSignalHeader(text) {
  if (!text) return { header: null, rest: text }
  const lines = text.split('\n')
  const first = (lines[0] || '').trim()
  const m = first.match(/^([A-Z]{2,6}\s*[—-]\s*)?(BULLISH|BEARISH|NEUTRAL|DISAGREE)\s*$/i)
        || first.match(/^SIGNAL\s*:\s*(BULLISH|BEARISH|NEUTRAL|DISAGREE)\s*$/i)
  if (!m) return { header: null, rest: text }
  const tone = (m[2] || m[1]).toLowerCase()
  return { header: { raw: first, tone }, rest: lines.slice(1).join('\n').trimStart() }
}

// / format llm prose
function LLMBody({ text }) {
  if (!text) return null
  const { header, rest } = splitSignalHeader(text)
  const paragraphs = rest.split(/\n{2,}/).filter(p => p.trim().length > 0)
  return (
    <>
      {header && (
        <div className={`llm-head ${header.tone}`.trim()}>{header.raw}</div>
      )}
      {paragraphs.map((para, pi) => {
        const lines = para.split('\n').filter(l => l.trim().length > 0)
        if (lines.length === 0) return null
        const trimmed = lines.map(l => l.trim())
        const isBulletList = trimmed.every(l => /^[•\-*]\s+/.test(l))
        const isNumberedList = trimmed.length >= 2 && trimmed.every(l => /^\d+\.\s+/.test(l))
        if (isBulletList) {
          return (
            <ul key={pi} className="llm-list">
              {trimmed.map((l, li) => (
                <li key={li}>{renderInline(l.replace(/^[•\-*]\s+/, ''))}</li>
              ))}
            </ul>
          )
        }
        if (isNumberedList) {
          return (
            <ol key={pi} className="llm-list">
              {trimmed.map((l, li) => (
                <li key={li}>{renderInline(l.replace(/^\d+\.\s+/, ''))}</li>
              ))}
            </ol>
          )
        }
        return (
          <p key={pi} className="llm-p">
            {lines.map((l, li) => (
              <span key={li}>
                {renderInline(l)}
                {li < lines.length - 1 && <br />}
              </span>
            ))}
          </p>
        )
      })}
    </>
  )
}

function LLMColumn({ name, model, signal, body }) {
  return (
    <div className="llm-col">
      <div className="h">
        <span className="nm">{name} · <b>{model || '—'}</b></span>
        {signal && <Pill variant={signalVariant(signal)}>{signal}</Pill>}
      </div>
      <div className="body">
        {body
          ? <LLMBody text={body} />
          : <span className="dim">Pending. Next analyst cycle in ~30 min.</span>}
      </div>
    </div>
  )
}

export default function DualLLMColumn({ score }) {
  const details = (score?.details && typeof score.details === 'object') ? score.details : {}
  const groqText = details.llm_analysis_groq
  const deepseekText = details.llm_analysis_deepseek

  if (!groqText && !deepseekText) {
    return <div className="dim" style={{ padding: 14, fontSize: 12 }}>AI analysis not yet available for this symbol.</div>
  }

  return (
    <div className="grid c2">
      <LLMColumn
        name="groq"
        model={details.llm_model_groq || 'llama-3.3-70b-versatile'}
        signal={details.llm_signal_groq}
        body={groqText}
      />
      <LLMColumn
        name="deepseek"
        model={details.llm_model_deepseek || 'deepseek-chat'}
        signal={details.llm_signal_deepseek}
        body={deepseekText}
      />
    </div>
  )
}
