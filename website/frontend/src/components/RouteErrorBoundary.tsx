import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

export class RouteErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('Zephyr route failed', error, info) }
  render() { return this.state.failed ? <main className="app"><section className="glass glass-regular" data-glass="1"><h1>Something went wrong</h1><p className="muted">Try reloading this page. Your server settings were not changed.</p><Link className="ios-button secondary" to="/">Back home</Link></section></main> : this.props.children }
}
