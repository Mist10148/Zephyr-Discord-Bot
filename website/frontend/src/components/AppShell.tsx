import { Link, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { ThemeToggle } from './ThemeToggle'

// A single sticky top bar above every page. It is the app-wide navigation: the
// wordmark returns home, a contextual link jumps to the server list once the user
// is in the dashboard, and the theme toggle lives on the right. Pages still render
// their own <main className="app"> beneath it, so this wraps rather than replaces
// their layout. The bar is glass so the aurora shows through it.
export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  const inDashboard = pathname.startsWith('/g')
  return (
    <>
      {/* Three independently drifting blobs rather than one shared gradient, so the
          backdrop reads as moving air. Fixed, behind everything, inert to pointers. */}
      <div className="aurora" aria-hidden><i /><i /><i /></div>
      <header className="appbar">
        <div className="appbar-inner">
          <Link to="/" className="brand" aria-label="Zephyr home">
            <span className="brand-mark" aria-hidden>❍</span>
            <span className="brand-name">Zephyr</span>
          </Link>
          <nav className="appbar-nav">
            {inDashboard && <Link to="/g" className="nav-link">Servers</Link>}
            <ThemeToggle />
          </nav>
        </div>
      </header>
      {children}
    </>
  )
}
