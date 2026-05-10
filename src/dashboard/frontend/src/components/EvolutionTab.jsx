import { useState } from 'react'
import SectionH from './ui/SectionH'
import MutationLogPanel from './evolution/MutationLogPanel'
import WikiRetrievalPanel from './evolution/WikiRetrievalPanel'
import ConfigDiffPanel from './evolution/ConfigDiffPanel'
import { useApi } from '../hooks/useApi'

// / mutation + retrieval + diff

function timeUntil(ts) {
  if (!ts) return '—'
  const diff = new Date(ts).getTime() - Date.now()
  if (diff < 0) return 'due'
  const h = Math.floor(diff / 3600000)
  const m = Math.floor((diff % 3600000) / 60000)
  if (h < 24) return `${h}h ${m}m`
  return `${Math.floor(h / 24)}d`
}

function lastEventTs(events) {
  if (!Array.isArray(events) || events.length === 0) return null
  let latest = null
  for (const e of events) {
    const t = e.created_at ? new Date(e.created_at).getTime() : 0
    if (t && (!latest || t > latest)) latest = t
  }
  return latest ? new Date(latest).toISOString() : null
}

export default function EvolutionTab({ evolution, loading }) {
  const { data: loops } = useApi('/api/loops', 60000)
  const evoLoop = loops?.loops?.find((l) => l.name === 'evolution')
  const lastTs = lastEventTs(evolution)
  const [selectedCycleId, setSelectedCycleId] = useState(null)

  return (
    <>
      <SectionH
        num="07"
        title="evolution"
        em="last cycle"
        meta={
          <>
            {lastTs ? lastTs.replace('T', ' ').slice(0, 16) : '—'}
            {evoLoop && <> · next in <span className="acc">{timeUntil(evoLoop.next_fire_ts)}</span></>}
          </>
        }
      >
        <div className="grid c12-8-4" style={{ alignItems: 'start' }}>
          <MutationLogPanel
            events={evolution || []}
            loading={loading}
            onSelectCycle={setSelectedCycleId}
            selectedCycleId={selectedCycleId}
          />
          <div style={{ position: 'sticky', top: 12 }}>
            <WikiRetrievalPanel cycleId={selectedCycleId} />
          </div>
        </div>
      </SectionH>

      <SectionH
        num="07b"
        title="config diff"
        em="parent → child"
        meta="latest mutation lineage"
      >
        <ConfigDiffPanel events={evolution || []} />
      </SectionH>
    </>
  )
}
