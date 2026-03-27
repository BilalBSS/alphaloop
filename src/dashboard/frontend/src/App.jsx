import { useState, useEffect } from 'react'
import { useApi, useWebSocket } from './hooks/useApi'
import Header from './components/Header'
import PortfolioTab from './components/PortfolioTab'
import TradesTab from './components/TradesTab'
import EvolutionTab from './components/EvolutionTab'
import HealthTab from './components/HealthTab'

const TABS = ['Portfolio', 'Trades', 'Evolution', 'Health']

export default function App() {
  const [activeTab, setActiveTab] = useState(() =>
    localStorage.getItem('qts-tab') || 'Portfolio'
  )
  const { status: wsStatus } = useWebSocket()

  const portfolio = useApi('/api/portfolio', 30000)
  const trades = useApi('/api/trades?limit=100', 30000)
  const strategies = useApi('/api/strategies', 60000)
  const evolution = useApi('/api/evolution', 60000)
  const health = useApi('/api/health', 60000)

  useEffect(() => {
    localStorage.setItem('qts-tab', activeTab)
  }, [activeTab])

  // keyboard shortcut: 1-4 to switch tabs
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
      <main className="flex-1 p-2">
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
      </main>
    </div>
  )
}
