import { Component } from 'react'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null, info: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    this.setState({ info })
    console.error('[ErrorBoundary]', this.props.label || 'tab', error, info?.componentStack)
  }

  handleReload = () => {
    window.location.reload()
  }

  render() {
    if (!this.state.error) return this.props.children

    const { error, info } = this.state
    const label = this.props.label || 'tab'
    const message = error?.message || String(error)
    const stack = error?.stack || ''
    const componentStack = info?.componentStack || ''

    return (
      <div className="bg-bg-surface border border-loss rounded-md p-4 m-4">
        <div className="text-sm font-semibold text-loss mb-2">
          {label} failed to render
        </div>
        <div className="text-xs text-text-secondary mb-3">
          this tab hit an unrecoverable error. other tabs should still work.
          open devtools console for the full stack.
        </div>
        <pre className="text-xs text-text-primary bg-bg-primary border border-border rounded p-2 overflow-auto max-h-40 mb-3">
          {message}
        </pre>
        {(stack || componentStack) && (
          <details className="text-xs text-text-muted mb-3">
            <summary className="cursor-pointer select-none">stack trace</summary>
            <pre className="mt-2 overflow-auto max-h-60 whitespace-pre-wrap">
              {stack}
              {componentStack && '\n\ncomponent stack:' + componentStack}
            </pre>
          </details>
        )}
        <button
          onClick={this.handleReload}
          className="px-3 py-1.5 text-xs font-medium bg-accent text-white rounded hover:opacity-90"
        >
          reload page
        </button>
      </div>
    )
  }
}
