// / dark theme for lightweight-charts matching the dashboard palette (index.css tokens)
// / used by useCandlestickChart to style chart layout, axes, candles, and volume histogram

export const darkTheme = {
  bg: '#12121a',
  border: '#1e1e2a',
  axisText: '#8888a0',
  grid: '#1a1a25',
  up: '#00dc82',
  down: '#ff4757',
  volumeUp: 'rgba(0, 220, 130, 0.5)',
  volumeDown: 'rgba(255, 71, 87, 0.5)',
  crosshair: '#8888a0',
}

// / maps darkTheme into a lightweight-charts ChartOptions object
export function chartLayoutOptions(theme) {
  return {
    layout: {
      background: { type: 'solid', color: theme.bg },
      textColor: theme.axisText,
      fontSize: 11,
      fontFamily: "'JetBrains Mono', ui-monospace, monospace",
    },
    grid: {
      vertLines: { color: theme.grid },
      horzLines: { color: theme.grid },
    },
    rightPriceScale: { borderColor: theme.border },
    timeScale: { borderColor: theme.border, timeVisible: true, secondsVisible: false },
    crosshair: {
      vertLine: { color: theme.crosshair, width: 1, style: 3 },
      horzLine: { color: theme.crosshair, width: 1, style: 3 },
    },
  }
}
