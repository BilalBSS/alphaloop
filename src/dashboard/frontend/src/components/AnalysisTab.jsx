import { useState, useEffect, useCallback } from 'react'
import { useApi } from '../hooks/useApi'
import Panel from './Panel'
import SymbolList, { SynthesisPanel, StrategyEvalPanel } from './analysis/SymbolList'
import SymbolDetail from './analysis/SymbolDetail'

// / main tab: synthesis + symbol list or detail view
export default function AnalysisTab() {
  const [selectedSymbol, setSelectedSymbol] = useState(null)
  const symbols = useApi('/api/symbols', 60000)

  // / browser back button support
  const selectSymbol = useCallback((sym) => {
    setSelectedSymbol(sym)
    if (sym) window.history.pushState({ symbol: sym }, '')
  }, [])

  useEffect(() => {
    const onPop = () => setSelectedSymbol(null)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  if (selectedSymbol) {
    return (
      <SymbolDetail
        key={selectedSymbol}
        symbol={selectedSymbol}
        onBack={() => { window.history.back() }}
      />
    )
  }

  return (
    <div className="space-y-2">
      <Panel title="Daily Synthesis">
        <SynthesisPanel onSelect={selectSymbol} />
      </Panel>
      <Panel title="Strategy Evaluation">
        <StrategyEvalPanel onSelect={selectSymbol} />
      </Panel>
      <Panel title="Symbol Analysis">
        <SymbolList
          symbols={symbols.data}
          loading={symbols.loading}
          onSelect={selectSymbol}
        />
      </Panel>
    </div>
  )
}
