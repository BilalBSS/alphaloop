// / runtime feature flag helper — read window globals first, then localStorage, then default
// / runtime (not build-time) so rollback is a browser toggle, not a rebuild+redeploy
// / flags:
// /   USE_LWC_CHART — boolean, gates tradingview lightweight-charts replacement for recharts intraday chart

const LWC_KEY = 'USE_LWC_CHART'

// / explicit true/false parsing — any other value returns undefined so resolve falls through
function _coerceBool(raw) {
  if (raw === undefined || raw === null) return undefined
  if (raw === true || raw === 'true' || raw === 1 || raw === '1') return true
  if (raw === false || raw === 'false' || raw === 0 || raw === '0') return false
  return undefined
}

function _readWindow(key) {
  if (typeof window === 'undefined') return undefined
  return _coerceBool(window[`__${key}`])
}

function _readStorage(key) {
  if (typeof window === 'undefined' || !window.localStorage) return undefined
  try {
    return _coerceBool(window.localStorage.getItem(key))
  } catch {
    return undefined
  }
}

function _resolve(key, fallback) {
  const fromWindow = _readWindow(key)
  if (fromWindow !== undefined) return fromWindow
  const fromStorage = _readStorage(key)
  if (fromStorage !== undefined) return fromStorage
  return fallback
}

export function isLWCChartEnabled() {
  // / default on — rolling out lightweight-charts as the new intraday renderer
  return _resolve(LWC_KEY, true)
}

export function setLWCChartEnabled(enabled) {
  if (typeof window === 'undefined' || !window.localStorage) return
  try {
    window.localStorage.setItem(LWC_KEY, enabled ? 'true' : 'false')
  } catch {}
}

export function resetLWCChartFlag() {
  if (typeof window === 'undefined' || !window.localStorage) return
  try {
    window.localStorage.removeItem(LWC_KEY)
  } catch {}
}
