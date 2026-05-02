import SectionH from './ui/SectionH'
import ProcessesPanel from './health/ProcessesPanel'
import DataFeedsPanel from './health/DataFeedsPanel'
import LLMLatencyTiles from './health/LLMLatencyTiles'
import CostTrackerTiles from './health/CostTrackerTiles'
import EnvHealthPanel from './health/EnvHealthPanel'
import LoopsPanel from './health/LoopsPanel'
import FeatureBenchmarkPanel from './health/FeatureBenchmarkPanel'

// / health + ops surfaces

export default function HealthTab({ health, loading }) {
  if (loading && !health) {
    return (
      <SectionH num="11" title="services" em="loading">
        <div className="empty-state"><div className="empty-state-title">loading health</div></div>
      </SectionH>
    )
  }

  return (
    <>
      <SectionH
        num="11"
        title="services"
        em="processes + feeds"
        meta={<><code>/api/health</code> · 60s poll</>}
      >
        <div className="grid c2">
          <ProcessesPanel health={health} />
          <DataFeedsPanel />
        </div>
      </SectionH>

      <SectionH num="11b" title="llm activity" em="calls per provider">
        <LLMLatencyTiles health={health} />
      </SectionH>

      <SectionH
        num="11c"
        title="cost"
        em="tracker"
        meta="budget cap $25/mo"
      >
        <CostTrackerTiles />
      </SectionH>

      <SectionH num="11d" title="env health" em="required + optional vars">
        <EnvHealthPanel />
      </SectionH>

      <SectionH num="11e" title="loops" em="cron + cadence registry">
        <LoopsPanel />
      </SectionH>

      <SectionH num="11f" title="feature benchmark" em="handbuilt vs alpha158">
        <FeatureBenchmarkPanel />
      </SectionH>
    </>
  )
}
