// / shared time-snap helper: given an iso/date string, return the time (unix seconds) of the
// / nearest candle so markers align to the chart axis. returns null if candles are empty or ts invalid.
// / candles[] must be sorted ascending by time (lightweight-charts requirement)
export function snapToCandle(iso, candles) {
  if (!iso || !Array.isArray(candles) || candles.length === 0) return null
  const target = Math.floor(new Date(iso).getTime() / 1000)
  if (!Number.isFinite(target)) return null
  // / clamp outside-range markers to first/last candle
  if (target <= candles[0].time) return candles[0].time
  if (target >= candles[candles.length - 1].time) return candles[candles.length - 1].time
  // / binary search for the closest candle
  let lo = 0
  let hi = candles.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (candles[mid].time < target) lo = mid + 1
    else hi = mid
  }
  // / lo now points to first candle >= target; check predecessor for closer match
  const after = candles[lo].time
  const before = lo > 0 ? candles[lo - 1].time : after
  return (target - before) <= (after - target) ? before : after
}
