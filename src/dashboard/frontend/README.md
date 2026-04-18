# Dashboard Frontend

React + Vite + Tailwind. Charts via [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts).

## Dev

```bash
npm install
npm run dev   # http://localhost:5173, proxies /api and /ws to http://localhost:8000
```

Backend must be running separately: `uvicorn src.dashboard.app:app --reload` from the repo root.

## Build

```bash
npm run build   # output in dist/, served by FastAPI at /
```

## Structure

- `src/App.jsx` — top-level tab router + WebSocket provider
- `src/components/<Tab>Tab.jsx` — one file per tab (Portfolio, Trades, Evolution, Health, Analysis, Knowledge)
- `src/components/Panel.jsx` — shared card container with loading/error/empty states
- `src/components/Skeleton.jsx` — skeleton loaders
- `src/hooks/useApi.js` — REST fetch hook + WebSocket hook
- `src/components/chart/` — candlestick + equity curve charts

## Adding a panel

1. Create `src/components/MyPanel.jsx`
2. Use `const { data, loading, error } = useApi('/api/my-endpoint', 30000)`
3. Wrap output in `<Panel title="..."><SkeletonTable /> or <Chart /></Panel>`
4. Import into the relevant tab component

## Conventions

- No CSS modules or styled-components. Tailwind classes + CSS custom properties in `index.css`.
- All state is component-local + `useApi`. No Redux / Zustand.
- Error states: show `error` in Panel; don't render partial data.

See `docs/ARCHITECTURE.md` at the repo root for backend + full architecture.
