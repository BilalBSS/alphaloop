import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// / phase 7 tier 2d: manual chunk split. keeps main <300kB by pulling each
// / heavy tab out of the entrypoint into its own hashed chunk (loaded on first
// / tab activation). vendor libs get their own chunk too so they cache across
// / app releases.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../static',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          // / pull each tab into its own chunk named after the tab
          if (id.includes('/components/PortfolioTab')) return 'tab-portfolio'
          if (id.includes('/components/TradesTab'))    return 'tab-trades'
          if (id.includes('/components/EvolutionTab')) return 'tab-evolution'
          if (id.includes('/components/HealthTab'))    return 'tab-health'
          if (id.includes('/components/AnalysisTab'))  return 'tab-analysis'
          if (id.includes('/components/KnowledgeTab')) return 'tab-knowledge'
          if (id.includes('/components/MacroTab'))     return 'tab-macro'
          if (id.includes('/components/SystemTab'))    return 'tab-system'
          // / chart stack is used across tabs; share it
          if (id.includes('/components/chart/'))       return 'chart'
          // / vendor chunk for heavy deps (lightweight-charts, react, etc.)
          if (id.includes('node_modules')) {
            if (id.includes('lightweight-charts')) return 'vendor-charts'
            if (id.includes('react') || id.includes('scheduler')) return 'vendor-react'
            return 'vendor'
          }
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
