import Pill from '../ui/Pill'

// / dual-llm side-by-side

function signalVariant(s) {
  if (s === 'bullish') return 'bullish'
  if (s === 'bearish') return 'bearish'
  return 'neutral'
}

function LLMColumn({ name, model, signal, body }) {
  return (
    <div className="llm-col">
      <div className="h">
        <span className="nm">{name} · <b>{model || '—'}</b></span>
        {signal && <Pill variant={signalVariant(signal)}>{signal}</Pill>}
      </div>
      <div className="body">
        {body || <span className="dim">Pending. Next analyst cycle in ~30 min.</span>}
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
