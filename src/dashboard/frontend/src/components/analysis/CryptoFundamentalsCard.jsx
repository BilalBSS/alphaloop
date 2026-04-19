import { useApi } from '../../hooks/useApi'
import { fmtLargeNum, fmtCount } from './formatters'

// / tooltip copy for each metric — kept concise so hover doesn't obscure the grid
const TOOLTIPS = {
  nvt_ratio: 'Network Value to Transactions — market cap / daily volume. Lower = more utility, higher = speculative premium.',
  funding_rate: 'Annualized perp funding rate across exchanges. Positive = longs pay shorts (bullish crowding), negative = shorts pay longs.',
  active_addresses: 'Unique on-chain addresses active in the last 24h. Proxy for network usage.',
  exchange_inflow_usd: 'Net USD flowing into major exchanges (inflow − outflow, 24h). Positive = potential sell pressure.',
  hash_rate: 'Mining hash rate (BTC only). Higher = more network security + miner commitment.',
  tvl_usd: 'Total Value Locked in DeFi protocols on this chain (via DefiLlama). Pure L1s with no DeFi show —.',
  dex_volume_24h: 'Decentralized exchange trading volume over 24h on this chain.',
  stablecoin_supply_ratio: 'Stablecoin market cap relative to total crypto mcap. Rising = dry powder accumulating, falling = capital rotating into risk.',
}

// / compact skeleton matching the grid layout so mount doesn't shift the page
function Skeleton() {
  return (
    <div className="grid grid-cols-2 gap-2">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="bg-bg-primary border border-border p-2">
          <div className="skeleton h-3 w-20 mb-2" />
          <div className="skeleton h-5 w-16" />
        </div>
      ))}
    </div>
  )
}

// / em dash for null values; formatter picks the right unit for non-null values
function formatMetric(key, val) {
  if (val == null) return '—'
  const n = parseFloat(val)
  if (!Number.isFinite(n)) return '—'
  switch (key) {
    case 'nvt_ratio':
      return n.toFixed(2)
    case 'funding_rate':
      return `${(n * 100).toFixed(3)}%`
    case 'active_addresses':
      return fmtCount(n)
    case 'exchange_inflow_usd':
      return (n >= 0 ? '+' : '') + fmtLargeNum(n)
    case 'hash_rate':
      return fmtLargeNum(n) + ' H/s'
    case 'tvl_usd':
      return fmtLargeNum(n)
    case 'dex_volume_24h':
      return fmtLargeNum(n)
    case 'stablecoin_supply_ratio':
      return `${(n * 100).toFixed(2)}%`
    default:
      return n.toString()
  }
}

// / directional color — only used for metrics with a clear bull/bear interpretation
function metricColor(key, val) {
  if (val == null) return 'text-text-muted'
  const n = parseFloat(val)
  if (!Number.isFinite(n)) return 'text-text-muted'
  if (key === 'funding_rate') {
    if (Math.abs(n) > 0.3) return n > 0 ? 'text-loss' : 'text-profit'
    return 'text-text-primary'
  }
  if (key === 'exchange_inflow_usd') {
    return n > 0 ? 'text-loss' : n < 0 ? 'text-profit' : 'text-text-primary'
  }
  return 'text-text-primary'
}

export default function CryptoFundamentalsCard({ symbol }) {
  const { data, loading, error, refetch } = useApi(`/api/crypto-fundamentals/${symbol}`, 300000)

  if (loading && !data) return <Skeleton />
  if (error) {
    return (
      <div className="space-y-2">
        <div className="text-loss text-sm py-2">Failed to load: {error}</div>
        <button
          onClick={refetch}
          className="text-[11px] uppercase border border-border px-2 py-1 text-text-secondary hover:text-text-primary"
        >
          Retry
        </button>
      </div>
    )
  }
  if (!data) return <div className="text-text-muted text-sm py-2">No fundamentals data</div>

  const metrics = [
    { key: 'nvt_ratio', label: 'NVT Ratio' },
    { key: 'funding_rate', label: 'Funding (annualized)' },
    { key: 'active_addresses', label: 'Active Addresses (24h)' },
    { key: 'exchange_inflow_usd', label: 'Net Exchange Inflow' },
    { key: 'hash_rate', label: 'Hash Rate' },
    { key: 'tvl_usd', label: 'Chain TVL' },
    { key: 'dex_volume_24h', label: 'DEX Volume (24h)' },
    { key: 'stablecoin_supply_ratio', label: 'Stablecoin SSR' },
  ]

  const allNull = metrics.every(m => data[m.key] == null)
  if (allNull) {
    return (
      <div className="space-y-2">
        <div className="text-text-muted text-sm py-2">Data sources offline — retry</div>
        <button
          onClick={refetch}
          className="text-[11px] uppercase border border-border px-2 py-1 text-text-secondary hover:text-text-primary"
        >
          Retry
        </button>
      </div>
    )
  }

  const sources = Array.isArray(data.sources) ? data.sources : []
  const updated = data.updated_at ? data.updated_at.replace('T', ' ').slice(0, 16) : null

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        {metrics.map(m => {
          const val = data[m.key]
          const display = formatMetric(m.key, val)
          const color = metricColor(m.key, val)
          return (
            <div key={m.key} className="bg-bg-primary border border-border p-2" title={TOOLTIPS[m.key]}>
              <div className="text-[10px] uppercase text-text-muted">{m.label}</div>
              <div className={`text-lg font-mono font-semibold ${val == null ? 'text-text-muted' : color}`}>
                {display}
              </div>
            </div>
          )
        })}
      </div>
      <div className="flex justify-between text-[10px] text-text-muted">
        <span>
          {sources.length > 0 ? `Sources: ${sources.join(', ')}` : 'Sources: none'}
        </span>
        {updated && <span>Updated: {updated}</span>}
      </div>
    </div>
  )
}
