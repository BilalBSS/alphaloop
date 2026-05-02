import { LineSeries, HistogramSeries, LineStyle } from 'lightweight-charts'

// / frontend counterpart of src/dashboard/indicator_registry.py
// / each entry: { id, pane, label, render(chart, payload, candles, paneIndex, priceSeries) -> entry }
// / entry shape: { seriesList, priceLines, update(payload, candles) }
// / panes -> pane indices: price=0, rsi=1, macd=2, stoch=3, adx=4, cci=5, williams=6, obv=7, mfi=8, atr=9, roc=10
// / splitting render from update lets IndicatorPane re-push data on the 60s refetch without
// / tearing down and recreating the series, which prevents flicker and wasted gpu work
// / horizontal_levels (fib_auto) attach priceLines on the price series rather than new series

export const PANE_INDEX = {
  price: 0,
  rsi: 1,
  macd: 2,
  stoch: 3,
  adx: 4,
  cci: 5,
  williams: 6,
  obv: 7,
  mfi: 8,
  atr: 9,
  roc: 10,
}

// / oscillator pane order drives paneIndex assignment when multiple oscillators are active
// / price pane is always 0; oscillators land at 1..N in the order they first appear
export const OSCILLATOR_PANES = ['rsi', 'macd', 'stoch', 'adx', 'cci', 'williams', 'obv', 'mfi', 'atr', 'roc']

const COLORS = {
  sma20: '#ffa94d',
  sma50: '#4dabf7',
  sma200: '#da77f2',
  ema20: '#ffd43b',
  ema50: '#69db7c',
  ema200: '#f06595',
  bbUpper: 'rgba(100, 149, 237, 0.75)',
  bbMiddle: 'rgba(100, 149, 237, 1)',
  bbLower: 'rgba(100, 149, 237, 0.75)',
  keltUpper: 'rgba(255, 170, 0, 0.75)',
  keltMid: 'rgba(255, 170, 0, 1)',
  keltLower: 'rgba(255, 170, 0, 0.75)',
  vwap: '#e599f7',
  ichiConv: '#ff6b6b',
  ichiBase: '#4dabf7',
  ichiSpanA: '#51cf66',
  ichiSpanB: '#ff6b6b',
  ichiLag: '#868e96',
  psarUp: '#7fb87a',
  psarDown: '#d56a5b',
  stUp: '#7fb87a',
  stDown: '#d56a5b',
  donUpper: 'rgba(136, 239, 255, 0.75)',
  donMid: 'rgba(136, 239, 255, 1)',
  donLower: 'rgba(136, 239, 255, 0.75)',
  fib: 'rgba(255, 215, 0, 0.6)',
  rsi: '#ffa94d',
  macdLine: '#4dabf7',
  macdSignal: '#ffa94d',
  macdHistUp: 'rgba(127, 184, 122, 0.65)',
  macdHistDown: 'rgba(213, 106, 91, 0.65)',
  stochK: '#ffa94d',
  stochD: '#4dabf7',
  adx: '#da77f2',
  cci: '#ffd43b',
  williams: '#f06595',
  obv: '#69db7c',
  mfi: '#ffa94d',
  atr: '#e599f7',
  roc: '#4dabf7',
}

// / build lightweight-charts line data arrays aligned to candles
// / skips entries where value is null / non-finite (warmup positions in the series)
function lineData(candles, values) {
  if (!Array.isArray(values) || values.length !== candles.length) return []
  const out = []
  for (let i = 0; i < candles.length; i++) {
    const v = values[i]
    if (v === null || v === undefined) continue
    if (typeof v !== 'number' || !Number.isFinite(v)) continue
    out.push({ time: candles[i].time, value: v })
  }
  return out
}

// / histogram data with per-bar color driven by sign (macd hist)
function histogramData(candles, values) {
  if (!Array.isArray(values) || values.length !== candles.length) return []
  const out = []
  for (let i = 0; i < candles.length; i++) {
    const v = values[i]
    if (v === null || v === undefined) continue
    if (typeof v !== 'number' || !Number.isFinite(v)) continue
    out.push({
      time: candles[i].time,
      value: v,
      color: v >= 0 ? COLORS.macdHistUp : COLORS.macdHistDown,
    })
  }
  return out
}

// / shared add-line helper with uniform defaults
function addLine(chart, paneIndex, opts) {
  return chart.addSeries(LineSeries, { lineWidth: 2, priceLineVisible: false, lastValueVisible: false, ...opts }, paneIndex)
}

// / generic single-value series renderer factory
function makeSingleSeries(color, paneKey, label) {
  return {
    pane: paneKey,
    label,
    render: (chart, payload, candles, paneIndex) => {
      const s = addLine(chart, paneIndex, { color, title: label })
      s.setData(lineData(candles, payload.values))
      return {
        seriesList: [s],
        priceLines: [],
        update: (p, c) => {
          if (p && p.values) s.setData(lineData(c, p.values))
        },
      }
    },
  }
}

// / price-pane overlay with a specific color key
function makeOverlay(colorKey, label) {
  return makeSingleSeries(COLORS[colorKey], 'price', label)
}

// / bollinger-style 3-line channel
function makeChannel(upperKey, midKey, lowerKey, label) {
  return {
    pane: 'price',
    label,
    render: (chart, payload, candles, paneIndex) => {
      const upper = addLine(chart, paneIndex, { color: COLORS[upperKey], lineWidth: 1, title: `${label} upper` })
      const mid = addLine(chart, paneIndex, { color: COLORS[midKey], lineWidth: 1, lineStyle: LineStyle.Dashed, title: `${label} mid` })
      const lower = addLine(chart, paneIndex, { color: COLORS[lowerKey], lineWidth: 1, title: `${label} lower` })
      const update = (p, c) => {
        if (!p) return
        upper.setData(lineData(c, p.upper))
        mid.setData(lineData(c, p.middle))
        lower.setData(lineData(c, p.lower))
      }
      update(payload, candles)
      return { seriesList: [upper, mid, lower], priceLines: [], update }
    },
  }
}

// / registry: id -> spec
export const INDICATORS = {
  // / price-pane overlays — moving averages
  sma_20: makeOverlay('sma20', 'SMA 20'),
  sma_50: makeOverlay('sma50', 'SMA 50'),
  sma_200: makeOverlay('sma200', 'SMA 200'),
  ema_20: makeOverlay('ema20', 'EMA 20'),
  ema_50: makeOverlay('ema50', 'EMA 50'),
  ema_200: makeOverlay('ema200', 'EMA 200'),

  // / bollinger 20/2
  bb_20_2: makeChannel('bbUpper', 'bbMiddle', 'bbLower', 'BB'),

  // / keltner 20/10/2
  keltner_20_10_2: makeChannel('keltUpper', 'keltMid', 'keltLower', 'Keltner'),

  // / donchian 20
  donchian_20: makeChannel('donUpper', 'donMid', 'donLower', 'Donchian'),

  // / vwap
  vwap: makeOverlay('vwap', 'VWAP'),

  // / ichimoku — 5 lines, cloud fill skipped (complex baseline logic deferred)
  ichimoku_9_26_52_26: {
    pane: 'price',
    label: 'Ichimoku',
    render: (chart, payload, candles, paneIndex) => {
      const conv = addLine(chart, paneIndex, { color: COLORS.ichiConv, lineWidth: 1, title: 'Tenkan' })
      const base = addLine(chart, paneIndex, { color: COLORS.ichiBase, lineWidth: 1, title: 'Kijun' })
      const spanA = addLine(chart, paneIndex, { color: COLORS.ichiSpanA, lineWidth: 1, title: 'Senkou A' })
      const spanB = addLine(chart, paneIndex, { color: COLORS.ichiSpanB, lineWidth: 1, title: 'Senkou B' })
      const lag = addLine(chart, paneIndex, { color: COLORS.ichiLag, lineWidth: 1, lineStyle: LineStyle.Dotted, title: 'Chikou' })
      const update = (p, c) => {
        if (!p) return
        conv.setData(lineData(c, p.conversion))
        base.setData(lineData(c, p.base))
        spanA.setData(lineData(c, p.span_a))
        spanB.setData(lineData(c, p.span_b))
        lag.setData(lineData(c, p.lagging))
      }
      update(payload, candles)
      return { seriesList: [conv, base, spanA, spanB, lag], priceLines: [], update }
    },
  },

  // / parabolic sar — lightweight-charts has no native dots, render as a thin dotted line
  // / tradeoff: loses the classic dot-per-bar look but preserves trajectory
  psar_2_20: {
    pane: 'price',
    label: 'PSAR',
    render: (chart, payload, candles, paneIndex) => {
      const s = addLine(chart, paneIndex, { color: COLORS.psarUp, lineWidth: 1, lineStyle: LineStyle.Dotted, title: 'PSAR' })
      const update = (p, c) => {
        if (!p || !p.sar) return
        s.setData(lineData(c, p.sar))
      }
      update(payload, candles)
      return { seriesList: [s], priceLines: [], update }
    },
  },

  // / supertrend — single line, color reflects latest direction (updated on each data push)
  supertrend_10_3: {
    pane: 'price',
    label: 'Supertrend',
    render: (chart, payload, candles, paneIndex) => {
      const resolveColor = (p) => {
        const dir = Array.isArray(p?.direction) ? p.direction : []
        let lastDir = 1
        for (let i = dir.length - 1; i >= 0; i--) {
          const d = dir[i]
          if (d === null || d === undefined) continue
          if (typeof d === 'number' && Number.isFinite(d)) { lastDir = d; break }
        }
        return lastDir >= 0 ? COLORS.stUp : COLORS.stDown
      }
      const s = addLine(chart, paneIndex, { color: resolveColor(payload), lineWidth: 2, title: 'Supertrend' })
      const update = (p, c) => {
        if (!p) return
        // / reapply color in case direction flipped since last push
        s.applyOptions({ color: resolveColor(p) })
        s.setData(lineData(c, p.line))
      }
      update(payload, candles)
      return { seriesList: [s], priceLines: [], update }
    },
  },

  // / fib auto — horizontal_levels rendered as priceLines on the price series
  // / update removes old priceLines and creates fresh ones so level shifts reflect without flicker
  fib_auto_100: {
    pane: 'price',
    label: 'Fib Auto',
    render: (chart, payload, candles, paneIndex, priceSeries) => {
      if (!priceSeries) return { seriesList: [], priceLines: [], update: () => {} }
      const state = { priceLines: [], priceSeries }
      const labels = { level_236: '0.236', level_382: '0.382', level_500: '0.500', level_618: '0.618', level_786: '0.786' }
      const apply = (p) => {
        for (const pl of state.priceLines) {
          try { state.priceSeries.removePriceLine(pl) } catch { /* / disposed */ }
        }
        state.priceLines = []
        if (!p || !p.levels) return
        for (const [k, title] of Object.entries(labels)) {
          const price = Number(p.levels[k])
          if (!Number.isFinite(price)) continue
          const pl = state.priceSeries.createPriceLine({
            price,
            color: COLORS.fib,
            lineStyle: LineStyle.Dashed,
            lineWidth: 1,
            axisLabelVisible: true,
            title,
          })
          state.priceLines.push(pl)
        }
      }
      apply(payload)
      return {
        seriesList: [],
        // / priceLines is returned-by-reference so IndicatorPane cleanup uses the latest set
        get priceLines() { return state.priceLines },
        update: (p) => apply(p),
      }
    },
  },

  // / oscillator panes — single-value
  rsi_14: makeSingleSeries(COLORS.rsi, 'rsi', 'RSI 14'),
  adx_14: makeSingleSeries(COLORS.adx, 'adx', 'ADX 14'),
  cci_20: makeSingleSeries(COLORS.cci, 'cci', 'CCI 20'),
  williams_14: makeSingleSeries(COLORS.williams, 'williams', 'Williams %R 14'),
  obv: makeSingleSeries(COLORS.obv, 'obv', 'OBV'),
  mfi_14: makeSingleSeries(COLORS.mfi, 'mfi', 'MFI 14'),
  atr_14: makeSingleSeries(COLORS.atr, 'atr', 'ATR 14'),
  roc_12: makeSingleSeries(COLORS.roc, 'roc', 'ROC 12'),

  // / macd — line + signal + histogram
  macd_12_26_9: {
    pane: 'macd',
    label: 'MACD',
    render: (chart, payload, candles, paneIndex) => {
      const hist = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false, title: 'Hist' }, paneIndex)
      const line = addLine(chart, paneIndex, { color: COLORS.macdLine, lineWidth: 2, title: 'MACD' })
      const sig = addLine(chart, paneIndex, { color: COLORS.macdSignal, lineWidth: 2, title: 'Signal' })
      const update = (p, c) => {
        if (!p) return
        hist.setData(histogramData(c, p.hist))
        line.setData(lineData(c, p.line))
        sig.setData(lineData(c, p.signal))
      }
      update(payload, candles)
      return { seriesList: [hist, line, sig], priceLines: [], update }
    },
  },

  // / stochastic — %k orange, %d blue
  stoch_14_3_3: {
    pane: 'stoch',
    label: 'Stoch',
    render: (chart, payload, candles, paneIndex) => {
      const k = addLine(chart, paneIndex, { color: COLORS.stochK, lineWidth: 2, title: '%K' })
      const d = addLine(chart, paneIndex, { color: COLORS.stochD, lineWidth: 2, title: '%D' })
      const update = (p, c) => {
        if (!p) return
        k.setData(lineData(c, p.k))
        d.setData(lineData(c, p.d))
      }
      update(payload, candles)
      return { seriesList: [k, d], priceLines: [], update }
    },
  },
}

// / count unique oscillator panes referenced by an indicator id list
// / used by LWChart to size its container
export function countOscillatorPanes(ids) {
  if (!Array.isArray(ids) || ids.length === 0) return 0
  const seen = new Set()
  for (const id of ids) {
    const spec = INDICATORS[id]
    if (!spec) continue
    if (spec.pane === 'price') continue
    seen.add(spec.pane)
  }
  return seen.size
}

// / given an id list, assign each requested oscillator pane a stable pane index 1..N
// / pane index order follows OSCILLATOR_PANES to keep layout deterministic
export function buildPaneIndexMap(ids) {
  const map = { price: 0 }
  const requested = new Set()
  for (const id of ids || []) {
    const spec = INDICATORS[id]
    if (spec && spec.pane !== 'price') requested.add(spec.pane)
  }
  let next = 1
  for (const p of OSCILLATOR_PANES) {
    if (requested.has(p)) {
      map[p] = next
      next += 1
    }
  }
  return map
}
