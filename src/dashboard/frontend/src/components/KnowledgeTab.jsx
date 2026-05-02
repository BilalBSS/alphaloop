import SectionH from './ui/SectionH'
import HydrationPanel from './knowledge/HydrationPanel'
import KnowledgeFeed from './knowledge/KnowledgeFeed'

// / unified feed + hydration

export default function KnowledgeTab() {
  return (
    <>
      <SectionH
        num="09"
        title="knowledge base"
        em="notes the system writes to itself"
        meta={<><code>/api/wiki</code> · pgvector · click row to expand</>}
      >
        <HydrationPanel />
      </SectionH>

      <SectionH num="09b" title="feed" em="all entries">
        <KnowledgeFeed />
      </SectionH>
    </>
  )
}
