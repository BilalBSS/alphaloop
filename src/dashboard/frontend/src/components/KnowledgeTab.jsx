import { useState } from 'react'
import Panel from './Panel'
import WikiBrowser from './knowledge/WikiBrowser'
import PostMortemList from './knowledge/PostMortemList'
import RegimeShiftList from './knowledge/RegimeShiftList'

const SUB_TABS = ['Wiki', 'Post-Mortems', 'Regime Shifts']

export default function KnowledgeTab() {
  const [sub, setSub] = useState('Wiki')

  return (
    <Panel title="Knowledge Base">
      <div className="flex gap-1 mb-3 text-xs">
        {SUB_TABS.map((t) => (
          <button
            key={t}
            onClick={() => setSub(t)}
            className={`px-3 py-1.5 rounded border transition-colors
              ${sub === t
                ? 'bg-accent text-bg-primary border-accent'
                : 'bg-bg-primary text-text-secondary border-border hover:text-text-primary'
              }`}
          >
            {t}
          </button>
        ))}
      </div>

      {sub === 'Wiki' && <WikiBrowser />}
      {sub === 'Post-Mortems' && <PostMortemList />}
      {sub === 'Regime Shifts' && <RegimeShiftList />}
    </Panel>
  )
}
