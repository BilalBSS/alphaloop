// / force-layout corr cluster

import { useMemo } from 'react'

const W = 320
const H = 320
const ITERS = 80
const EDGE_THRESHOLD = 0.4

export default function CorrelationCluster({ symbols, matrix }) {
  const layout = useMemo(() => computeLayout(symbols, matrix), [symbols, matrix])
  if (!symbols || symbols.length < 2 || !matrix || matrix.length === 0) {
    return <div className="empty-state"><div className="empty-state-title">need ≥2 positions</div></div>
  }
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', display: 'block' }}>
      {layout.edges.map((e, i) => (
        <line
          key={i}
          x1={layout.nodes[e.a].x} y1={layout.nodes[e.a].y}
          x2={layout.nodes[e.b].x} y2={layout.nodes[e.b].y}
          stroke="var(--acc)" strokeWidth={Math.max(0.5, e.w * 2)}
          opacity={Math.min(1, 0.25 + e.w * 0.6)}
        />
      ))}
      {layout.nodes.map((n) => (
        <g key={n.sym} transform={`translate(${n.x},${n.y})`}>
          <circle r={6} fill="var(--bg-3)" stroke="var(--ink-2)" strokeWidth={1} />
          <text dy={-9} textAnchor="middle" fontFamily="var(--mono)" fontSize="10" fill="var(--ink-2)">{n.sym}</text>
        </g>
      ))}
    </svg>
  )
}

function computeLayout(symbols, matrix) {
  if (!symbols || symbols.length === 0) return { nodes: [], edges: [] }
  const n = symbols.length
  const cx = W / 2
  const cy = H / 2
  const r = Math.min(W, H) / 3
  const nodes = symbols.map((sym, i) => ({
    sym,
    x: cx + r * Math.cos((i / n) * 2 * Math.PI),
    y: cy + r * Math.sin((i / n) * 2 * Math.PI),
    vx: 0, vy: 0,
  }))
  const edges = []
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const v = matrix[i]?.[j]
      if (v != null && Math.abs(v) >= EDGE_THRESHOLD) {
        edges.push({ a: i, b: j, w: Math.abs(v) })
      }
    }
  }
  for (let it = 0; it < ITERS; it++) {
    for (let i = 0; i < n; i++) nodes[i].fx = nodes[i].fy = 0
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = nodes[i].x - nodes[j].x
        const dy = nodes[i].y - nodes[j].y
        const d2 = dx * dx + dy * dy + 0.01
        const repulse = 800 / d2
        nodes[i].fx += dx * repulse
        nodes[i].fy += dy * repulse
        nodes[j].fx -= dx * repulse
        nodes[j].fy -= dy * repulse
      }
    }
    for (const e of edges) {
      const a = nodes[e.a]
      const b = nodes[e.b]
      const dx = b.x - a.x
      const dy = b.y - a.y
      const d = Math.sqrt(dx * dx + dy * dy) || 1
      const target = 80 - e.w * 40
      const f = (d - target) * 0.05 * e.w
      a.fx += (dx / d) * f
      a.fy += (dy / d) * f
      b.fx -= (dx / d) * f
      b.fy -= (dy / d) * f
    }
    for (const node of nodes) {
      node.vx = (node.vx + node.fx) * 0.5
      node.vy = (node.vy + node.fy) * 0.5
      node.x += node.vx
      node.y += node.vy
      node.x = Math.max(20, Math.min(W - 20, node.x))
      node.y = Math.max(20, Math.min(H - 20, node.y))
    }
  }
  return { nodes, edges }
}
