import SectionH from './ui/SectionH'
import TradeLedger from './trades/TradeLedger'
import CloseToFiringPanel from './trades/CloseToFiringPanel'
import NearMissesPanel from './trades/NearMissesPanel'

// / fills + observation log

export default function TradesTab({ trades, loading, navigate }) {
  return (
    <>
      <SectionH
        num="08"
        title="fills"
        em="trade ledger"
        meta={<><code>/api/trades</code> · click row → reasoning · view → chain</>}
      >
        {loading ? (
          <div className="empty-state"><div className="empty-state-title">loading trades</div></div>
        ) : (
          <TradeLedger trades={trades || []} navigate={navigate} />
        )}
      </SectionH>

      <SectionH
        num="08b"
        title="observation log"
        em="near-misses · 24h"
        meta={<><code>/api/observation-log</code></>}
      >
        <div className="grid c2">
          <CloseToFiringPanel />
          <NearMissesPanel />
        </div>
      </SectionH>
    </>
  )
}
