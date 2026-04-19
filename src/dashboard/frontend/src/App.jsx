import { useState, useEffect } from 'react'
import { WebSocketProvider, useWebSocketContext } from './contexts/WebSocketContext'
import { useApiLive } from './hooks/useApiLive'
import Header from './components/Header'
import PortfolioTab from './components/PortfolioTab'
import TradesTab from './components/TradesTab'
import EvolutionTab from './components/EvolutionTab'
import HealthTab from './components/HealthTab'
import AnalysisTab from './components/AnalysisTab'
import KnowledgeTab from './components/KnowledgeTab'

const TABS = ['Portfolio', 'Trades', 'Evolution', 'Health', 'Analysis', 'Knowledge']

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
      <nav className="bg-bg-surface border-b border-border flex px-4 overflow-x-auto shrink-0">
        {TABS.map((tab, i) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2.5 text-sm transition-colors whitespace-nowrap
              ${activeTab === tab
                ? 'text-accent border-b-2 border-accent'
                : 'text-text-secondary hover:text-text-primary border-b-2 border-transparent'
              }`}
            aria-label={`${tab} tab (press ${i + 1})`}
          >
            {tab}
          </button>
        ))}
      </nav>

      {/* tab content */}
      <main className="flex-1 p-4 md:p-6">
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
        {activeTab === 'Analysis' && (
          <AnalysisTab />
        )}
        {activeTab === 'Knowledge' && (
          <KnowledgeTab />
        )}
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
