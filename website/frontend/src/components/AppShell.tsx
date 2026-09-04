import { Link, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { ThemeToggle } from './ThemeToggle'
import { RouteMeta } from './RouteMeta'
import { ToastHost } from './ToastHost'

// A single sticky top bar above every page. It is the app-wide navigation: the
// wordmark returns home, a contextual link jumps to the server list once the user
// is in the dashboard, and the palette trigger and theme toggle live on the right.
// Pages still render their own <main className="app"> beneath it, so this wraps
// rather than replaces their layout. The bar is glass so the aurora shows through.
export function AppShell({ children, onOpenPalette }: { children: ReactNode; onOpenPalette(): void }) {
  const { pathname } = useLocation()
  const inDashboard = pathname.startsWith('/g')
  const [online, setOnline] = useState(() => navigator.onLine)
  useEffect(() => { const update = () => setOnline(navigator.onLine); window.addEventListener('online', update); window.addEventListener('offline', update); return () => { window.removeEventListener('online', update); window.removeEventListener('offline', update) } }, [])
  // The palette is not mounted on the sign-in screen, so its trigger must not be
  // offered there either.
  const showPalette = pathname !== '/login'
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
          <div className="appbar-spacer" />
          <nav className="appbar-nav">
            {inDashboard && <Link to="/g" className="nav-link">Servers</Link>}
            {pathname !== '/login' && <Link to="/commands" className="nav-link">Commands</Link>}
            {pathname !== '/login' && <Link to="/settings" className="nav-link">Appearance</Link>}
            {/* ⌘K on its own is a secret. The pill makes the shortcut discoverable
                and gives pointer users a way into the palette at all. */}
            {showPalette && (
              <button type="button" className="palette-trigger" onClick={onOpenPalette} title="Search commands" aria-label="Search commands">
                <span className="lens" aria-hidden />
                <kbd aria-hidden>⌘K</kbd>
              </button>
            )}
            <ThemeToggle />
          </nav>
        </div>
      </header>
      <RouteMeta />
      <ToastHost />
      {!online && <div className="offline-banner" role="status">You’re offline. Saved public results remain available until you reconnect.</div>}
      <div id="main-content" tabIndex={-1}>{children}</div>
    </>
  )
}
