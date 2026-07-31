import { NavLink } from 'react-router-dom'
import type { ReactNode } from 'react'

// The per-server section nav. On desktop `.guild-shell` lays this out as a sticky
// left rail; on phones it becomes a horizontally scrolling row of pills at the top
// of the page, so jumping between a server's sections never means going back to the
// overview first.
const SECTIONS = [
  { to: '', label: 'Overview', end: true },
  { to: '/music', label: 'Music' },
  { to: '/weather-alerts', label: 'Weather' },
  { to: '/ai', label: 'AI' },
  { to: '/settings', label: 'Settings' },
  { to: '/audit', label: 'Audit' },
]

export function GuildNav({ guildId }: { guildId?: string }) {
  const base = `/g/${guildId ?? ''}`
  return (
    <nav className="guild-nav" aria-label="Server sections">
      {SECTIONS.map(section => (
        <NavLink key={section.to} to={`${base}${section.to}`} end={section.end} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          {section.label}
        </NavLink>
      ))}
    </nav>
  )
}

// Wraps a guild sub-page's body so it sits beside the section nav. Pages keep
// rendering their own header and content; this only provides the two-column frame.
export function GuildShell({ guildId, children }: { guildId?: string; children: ReactNode }) {
  return (
    <div className="guild-shell">
      <GuildNav guildId={guildId} />
      <div className="guild-main">{children}</div>
    </div>
  )
}
