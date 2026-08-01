import { NavLink, useLocation } from 'react-router-dom'
import { TabHomeIcon, TabServersIcon, TabSystemIcon, TabWeatherIcon } from './icons'

// The phone's primary navigation. Hidden above the 860px breakpoint by CSS, where
// the top bar already carries the same destinations.
//
// It is sticky rather than fixed so it lives inside the page's own scroll flow and
// can never sit on top of the last row of content; `.app` already reserves the
// safe-area inset at the bottom.
const TABS = [
  { to: '/', label: 'Home', Icon: TabHomeIcon, end: true },
  { to: '/weather', label: 'Weather', Icon: TabWeatherIcon },
  { to: '/g', label: 'Servers', Icon: TabServersIcon },
  { to: '/kitchen-sink', label: 'System', Icon: TabSystemIcon },
]

export function TabBar() {
  const { pathname } = useLocation()
  // Servers stays lit anywhere under /g -- a guild's music or settings page is
  // still "the servers section", and unlighting it there would make the tab bar
  // look like it had lost track of where the user is.
  const inGuildArea = pathname.startsWith('/g')
  if (pathname === '/login') return null
  return (
    <nav className="tab-bar" data-glass="1" aria-label="Sections">
      {TABS.map(({ to, label, Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) => (to === '/g' ? inGuildArea : isActive) ? 'active' : undefined}
        >
          <Icon />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
