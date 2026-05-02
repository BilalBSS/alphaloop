import { useState, useEffect, useCallback, lazy, Suspense } from 'react'
import { WebSocketProvider, useWebSocketContext } from './contexts/WebSocketContext'
import { useApi } from './hooks/useApi'
import { useApiLive } from './hooks/useApiLive'
import { useAdminToken } from './hooks/useAdminToken'
import Header from './components/Header'
import HeadStrip from './components/HeadStrip'
import ErrorBoundary from './components/ErrorBoundary'
import PendingTab from './components/PendingTab'

// / lazy-loaded tab chunks
const TodayTab      = lazy(() => import('./components/TodayTab'))
const PositionsTab  = lazy(() => import('./components/PositionsTab'))
const StrategiesTab = lazy(() => import('./components/StrategiesTab'))
const TradesTab     = lazy(() => import('./components/TradesTab'))
const EvolutionTab  = lazy(() => import('./components/EvolutionTab'))
const HealthTab     = lazy(() => import('./components/HealthTab'))
const AnalysisTab   = lazy(() => import('./components/AnalysisTab'))
const KnowledgeTab  = lazy(() => import('./components/KnowledgeTab'))
const MacroTab      = lazy(() => import('./components/MacroTab'))

// / v3 11-tab ia
const TABS = [
  { id: 'today',      label: 'today',      badge: '/01' },
  { id: 'positions',  label: 'positions',  badge: '/02' },
  { id: 'strategies', label: 'strategies', badge: '/03' },
  { id: 'analysis',   label: 'analysis',   badge: '/04' },
  { id: 'decisions',  label: 'decisions',  badge: '/05' },
  { id: 'risk',       label: 'risk & gates', badge: '/06' },
  { id: 'evolution',  label: 'evolution',  badge: '/07' },
  { id: 'trades',     label: 'trades',     badge: '/08' },
  { id: 'knowledge',  label: 'knowledge',  badge: '/09' },
  { id: 'macro',      label: 'macro',      badge: '/10' },
  { id: 'health',     label: 'health',     badge: '/11' },
]

function TabLoading() {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '64px 0', color: 'var(--ink-3)' }}>
      <div className="skeleton" style={{ height: 32, width: 160 }} />
    </div>
  )
}

function AppInner() {
  const [activeTab, setActiveTab] = useState(() => {
    const saved = localStorage.getItem('qts-tab-v3')
    if (saved && TABS.find(t => t.id === saved)) return saved
    return 'today'
  })
  const { status: wsStatus } = useWebSocketContext()
  const adminToken = useAdminToken()

  // / live data sources
  const portfolio = useApiLive('/api/portfolio', 30000, ['position_update', 'trade_executed'])
  const trades = useApiLive('/api/trades?limit=100', 30000, ['trade_executed'])
  const strategies = useApiLive('/api/strategies', 60000, ['strategy_status_change'])
  const evolution = useApiLive('/api/evolution', 60000, ['strategy_status_change'])
  const health = useApiLive('/api/health', 60000, [])
  const equityHistory = useApi('/api/equity-history', 60000)
  const macro = useApi('/api/macro-context', 60000)
  const tailDep = useApi('/api/portfolio/tail-dependence', 60000)
  const version = useApi('/api/version', 0)
  const pauseStatus = useApi('/api/admin/pause', 30000)

  const [pausedOverride, setPausedOverride] = useState(null)
  const paused = pausedOverride !== null
    ? pausedOverride
    : Boolean(pauseStatus?.data?.paused)

  useEffect(() => {
    localStorage.setItem('qts-tab-v3', activeTab)
  }, [activeTab])

  useEffect(() => {
    function handleKey(e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return
      const idx = parseInt(e.key, 10) - 1
      if (idx >= 0 && idx < TABS.length) setActiveTab(TABS[idx].id)
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [])

  const onRunCycle = useCallback(async () => {
    const token = adminToken.ensure()
    if (!token) return
    try {
      const r = await fetch('/api/admin/trigger/strategy', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (r.status === 401) {
        adminToken.clear()
        window.alert('admin token rejected; cleared')
      }
    } catch {
      window.alert('run cycle failed')
    }
  }, [adminToken])

  const onPauseExec = useCallback(async () => {
    const token = adminToken.ensure()
    if (!token) return
    try {
      const r = await fetch('/api/admin/pause', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ paused: !paused }),
      })
      if (r.ok) {
        const j = await r.json()
        setPausedOverride(Boolean(j.paused))
      } else if (r.status === 401) {
        adminToken.clear()
        window.alert('admin token rejected; cleared')
      }
    } catch {
      window.alert('pause toggle failed')
    }
  }, [paused, adminToken])

  const risk = {
    tail_dependence: tailDep?.data?.lambda ?? tailDep?.data?.tail_lambda,
    risk_budget: portfolio?.data?.risk_budget_used,
    var_95: portfolio?.data?.var_95,
    var_95_pct: portfolio?.data?.var_95_pct,
    current_drawdown: portfolio?.data?.drawdown,
    gates_passing: health?.data?.gates_passing,
  }

  return (
    <>
      <Header
        portfolio={portfolio.data}
        health={health.data}
        macro={macro?.data}
        version={version?.data}
        wsStatus={wsStatus}
        onRunCycle={onRunCycle}
        onPauseExec={onPauseExec}
        paused={paused}
      />

      <div className="shell">
        <HeadStrip
          portfolio={portfolio.data}
          equityHistory={equityHistory?.data}
          strategies={strategies.data}
          macro={macro?.data}
          risk={risk}
        />

        <nav className="tabs" id="tabs">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={activeTab === tab.id ? 'on' : ''}
              onClick={() => setActiveTab(tab.id)}
              aria-label={`${tab.label} (${tab.badge})`}
              aria-current={activeTab === tab.id ? 'page' : undefined}
            >
              {tab.label}
              <span className="badge">{tab.badge}</span>
            </button>
          ))}
        </nav>

        <main>
          <ErrorBoundary label={activeTab}>
            <Suspense fallback={<TabLoading />}>
              {activeTab === 'today' && (
                <TodayTab
                  portfolio={portfolio.data}
                  trades={trades.data}
                />
              )}
              {activeTab === 'positions' && (
                <PositionsTab portfolio={portfolio.data} />
              )}
              {activeTab === 'strategies' && (
                <StrategiesTab strategies={strategies.data} />
              )}
              {activeTab === 'analysis' && <AnalysisTab />}
              {activeTab === 'decisions' && (
                <PendingTab
                  title="decisions"
                  sectionNum="05"
                  phase="C5 pending"
                  hint="decision chain + gate trace arrives in C5 with the decision_id schema migration."
                />
              )}
              {activeTab === 'risk' && (
                <PendingTab
                  title="risk & gates"
                  sectionNum="06"
                  phase="C5 pending"
                  hint="four risk gauges + correlation cluster diagram + 8-gate trace arrives in C5."
                />
              )}
              {activeTab === 'evolution' && (
                <EvolutionTab evolution={evolution.data} loading={evolution.loading} />
              )}
              {activeTab === 'trades' && (
                <TradesTab trades={trades.data} loading={trades.loading} />
              )}
              {activeTab === 'knowledge' && <KnowledgeTab />}
              {activeTab === 'macro' && <MacroTab />}
              {activeTab === 'health' && (
                <HealthTab health={health.data} loading={health.loading} />
              )}
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
    </>
  )
}

export default function App() {
  return (
    <WebSocketProvider>
      <AppInner />
    </WebSocketProvider>
  )
}
