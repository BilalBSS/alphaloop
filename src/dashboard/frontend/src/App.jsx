import { useState, useEffect, lazy, Suspense } from 'react'
import { WebSocketProvider, useWebSocketContext } from './contexts/WebSocketContext'
import { useApiLive } from './hooks/useApiLive'
import Header from './components/Header'

// / code-split each tab so first paint ships ~header + portfolio only.
// / vite manualChunks config keeps each tab on its own hashed bundle that loads
// / on first activation. previously the dashboard shipped every tab at once
// / (~570kB main chunk); with route splitting each page is ~60-90kB.
const PortfolioTab = lazy(() => import('./components/PortfolioTab'))
const TradesTab    = lazy(() => import('./components/TradesTab'))
const EvolutionTab = lazy(() => import('./components/EvolutionTab'))
const HealthTab    = lazy(() => import('./components/HealthTab'))
const AnalysisTab  = lazy(() => import('./components/AnalysisTab'))
const KnowledgeTab = lazy(() => import('./components/KnowledgeTab'))
const SystemTab    = lazy(() => import('./components/SystemTab'))
const MacroTab     = lazy(() => import('./components/MacroTab'))

const TABS = ['Portfolio', 'Trades', 'Evolution', 'Health', 'Analysis', 'Knowledge', 'Macro', 'System']

// / suspense fallback shown while a tab chunk downloads on first activation
function TabLoading() {
  return (
    <div className="flex items-center justify-center py-16 text-text-secondary text-sm">
      <div className="skeleton h-8 w-40 rounded" />
    </div>
  )
}

function AppInner() {
  const [activeTab, setActiveTab] = useState(() =>
    localStorage.getItem('qts-tab') || 'Portfolio'
  )
  const { status: wsStatus } = useWebSocketContext()

  // / top-level queries — live refresh on relevant ws events
  const portfolio = useApiLive('/api/portfolio', 30000, ['position_update', 'trade_executed'])
  const trades = useApiLive('/api/trades?limit=100', 30000, ['trade_executed'])
  const strategies = useApiLive('/api/strategies', 60000, ['strategy_status_change'])
  const evolution = useApiLive('/api/evolution', 60000, ['strategy_status_change'])
  const health = useApiLive('/api/health', 60000, [])

  useEffect(() => {
    localStorage.setItem('qts-tab', activeTab)
  }, [activeTab])

  // / keyboard shortcut: 1-N to switch tabs
  useEffect(() => {
    function handleKey(e) {
      const idx = parseInt(e.key) - 1
      if (idx >= 0 && idx < TABS.length && !e.ctrlKey && !e.metaKey) {
        setActiveTab(TABS[idx])
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [])

  const loading = {
    portfolio: portfolio.loading,
    trades: trades.loading,
    strategies: strategies.loading,
    evolution: evolution.loading,
    health: health.loading,
  }

  return (
    <div className="min-h-screen bg-bg-primary flex flex-col">
      <Header portfolio={portfolio.data} wsStatus={wsStatus} />

      {/* tab navigation */}
      <nav className="bg-bg-surface border-b border-border flex px-6 gap-1 overflow-x-auto shrink-0">
        {TABS.map((tab, i) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-3 text-sm font-medium transition-colors whitespace-nowrap -mb-px
              ${activeTab === tab
                ? 'text-text-primary border-b-2 border-accent'
                : 'text-text-secondary hover:text-text-primary border-b-2 border-transparent'
              }`}
            aria-label={`${tab} tab (press ${i + 1})`}
          >
            {tab}
          </button>
        ))}
      </nav>

      {/* tab content — lazy loaded, each inside suspense boundary */}
      <main className="flex-1 p-4 md:p-6">
        <Suspense fallback={<TabLoading />}>
          {activeTab === 'Portfolio' && (
            <PortfolioTab
              portfolio={portfolio.data}
              trades={trades.data}
              strategies={strategies.data}
              loading={loading}
            />
          )}
          {activeTab === 'Trades' && (
            <TradesTab trades={trades.data} loading={loading.trades} />
          )}
          {activeTab === 'Evolution' && (
            <EvolutionTab evolution={evolution.data} loading={loading.evolution} />
          )}
          {activeTab === 'Health' && (
            <HealthTab health={health.data} loading={loading.health} />
          )}
          {activeTab === 'Analysis' && <AnalysisTab />}
          {activeTab === 'Knowledge' && <KnowledgeTab />}
          {activeTab === 'Macro' && <MacroTab />}
          {activeTab === 'System' && <SystemTab />}
        </Suspense>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <WebSocketProvider>
      <AppInner />
    </WebSocketProvider>
  )
}
