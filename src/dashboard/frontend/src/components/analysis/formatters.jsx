// / format large numbers to human-readable (currency)
export function fmtLargeNum(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '--'
  const abs = Math.abs(n)
  const prefix = n < 0 ? '-$' : '$'
  if (abs >= 1e12) return `${prefix}${(abs / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `${prefix}${(abs / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${prefix}${(abs / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${prefix}${(abs / 1e3).toFixed(1)}K`
  return `${prefix}${abs.toFixed(2)}`
}

// / format large counts without dollar sign (shares, units)
export function fmtCount(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '--'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1e12) return `${sign}${(abs / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(1)}K`
  return `${sign}${abs.toLocaleString()}`
}

// / format value for insider panel
export function fmtVal(v) {
  const n = parseFloat(v)
  if (v == null || isNaN(n) || n === 0) return '--'
  return n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : `$${(n / 1e3).toFixed(0)}K`
}

// / color helpers
export function scoreColor(v) {
  const n = parseFloat(v || 0)
  if (n >= 70) return 'text-profit'
  if (n >= 40) return 'text-warning'
  return 'text-loss'
}

export function consensusBadge(c) {
  const map = { bullish: 'text-profit', bearish: 'text-loss', neutral: 'text-warning', disagree: 'text-accent' }
  return (
    <span className={`px-2 py-0.5 text-xs font-semibold uppercase ${map[c] || 'text-text-muted'}`}>
      {c || '--'}
    </span>
  )
}

export function regimeBadge(r) {
  const map = { bull: 'text-profit', bear: 'text-loss', sideways: 'text-warning', high_vol: 'text-accent' }
  return (
    <span className={`px-2 py-0.5 text-xs font-semibold uppercase ${map[r] || 'text-text-muted'}`}>
      {r || '--'}
    </span>
  )
}
