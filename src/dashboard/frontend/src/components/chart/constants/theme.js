// / v3 chart theme

export const darkTheme = {
  bg: '#0b0d0c',
  border: '#232927',
  axisText: '#b8b3a3',
  grid: '#161a18',
  up: '#7fb87a',
  down: '#d56a5b',
  acc: '#d8b466',
  acc2: '#6fa8c9',
  volumeUp: 'rgba(127, 184, 122, 0.55)',
  volumeDown: 'rgba(213, 106, 91, 0.55)',
  crosshair: '#807b6d',
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
