import { useEffect, useState } from 'react'
import Card from '../ui/Card'
import Stream from '../ui/Stream'
import { useWebSocketContext } from '../../contexts/WebSocketContext'

// / live ws-driven event feed

const MAX_EVENTS = 60

const KIND_BY_TYPE = {
  trade_executed: 'fill',
  signal_generated: 'signal',
  regime_shift: 'regime',
  strategy_status_change: 'evolve',
}

function formatTs(d = new Date()) {
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

function describe(type, payload) {
  if (type === 'trade_executed') {
    const sym = payload.symbol ?? payload.sym ?? '—'
    const side = (payload.side ?? '').toUpperCase()
    const qty = payload.qty ?? payload.filled_qty
    const px = payload.price ?? payload.fill_price
    const text = (
      <><b>{sym}</b> {side ? `${side} ` : ''}{qty != null ? `${qty} ` : ''}{px != null ? `@ $${Number(px).toFixed(2)}` : ''}</>
    )
    const pnl = payload.pnl
    const delta = pnl != null ? `${pnl >= 0 ? '+' : ''}$${Number(pnl).toFixed(2)}` : ''
    return { text, delta }
  }
  if (type === 'signal_generated') {
    const sym = payload.symbol ?? '—'
    const side = (payload.side ?? '').toUpperCase()
    const sid = payload.strategy_id ?? ''
    return { text: <><b>{sym}</b> {side} signal{sid ? ` · ${sid}` : ''}</>, delta: '' }
  }
  if (type === 'regime_shift') {
    const from = payload.old_regime ?? payload.from ?? '—'
    const to = payload.new_regime ?? payload.to ?? '—'
    const conf = payload.confidence
    return { text: <>regime <b>{from}</b> → <b>{to}</b>{conf != null ? ` · p=${Number(conf).toFixed(2)}` : ''}</>, delta: '' }
  }
  if (type === 'strategy_status_change') {
    const sid = payload.strategy_id ?? '—'
    const status = payload.status ?? payload.new_status ?? '—'
    return { text: <><b>{sid}</b> → {status}</>, delta: '' }
  }
  return { text: type, delta: '' }
}

export default function ActivityStream() {
  const { subscribe } = useWebSocketContext()
  const [events, setEvents] = useState([])

  useEffect(() => {
    const types = Object.keys(KIND_BY_TYPE)
    const unsubs = types.map((type) => subscribe(type, (msg) => {
      const payload = msg?.payload ?? msg ?? {}
      const { text, delta } = describe(type, payload)
      const ev = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        ts: formatTs(),
        kind: KIND_BY_TYPE[type],
        text,
        delta,
      }
      setEvents((prev) => [ev, ...prev].slice(0, MAX_EVENTS))
    }))
    return () => unsubs.forEach((u) => u && u())
  }, [subscribe])

  return (
    <Card title={<><b>activity</b> · live</>} meta="ws · /ws">
      <Stream events={events} emptyMessage="waiting on first event" />
    </Card>
  )
}
