import { Component } from 'react'

// / error boundary wrapping lwchart — react requires a class component for componentDidCatch
// / if lightweight-charts throws (bad payload, stale series ref, v5 pane edge case) the fallback
// / is rendered so the surrounding analysis tab does not crash
// / fallback prop is a render function taking the caught error
export default class ChartErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // / log to the browser console for debugging — no external reporting in this project
    if (typeof console !== 'undefined' && console.error) {
      console.error('chart error boundary caught:', error, info)
    }
  }

  reset = () => {
    this.setState({ error: null })
  }

  render() {
    if (this.state.error) {
      const fallback = this.props.fallback
      if (typeof fallback === 'function') return fallback(this.state.error, this.reset)
      return (
        <div className="flex flex-col items-center justify-center h-48 text-text-muted text-sm gap-2">
          <span className="text-loss">Chart failed to render</span>
          <button onClick={this.reset} className="text-[11px] uppercase px-2 py-0.5 border border-border hover:border-accent">
            retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
