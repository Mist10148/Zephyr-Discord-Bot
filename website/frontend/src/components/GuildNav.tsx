import { useQuery } from '@tanstack/react-query'
import { NavLink } from 'react-router-dom'
import type { ReactNode } from 'react'
import { api } from '../lib/api'
import type { GuildOverview } from '../types/api'
import { GuildIcon } from './DiscordAvatar'

// The per-server section nav. On desktop `.guild-shell` lays this out as a sticky
// 208px left rail headed by the server's own icon and name; on phones it becomes a
// horizontally scrolling row of pills at the top of the page, so jumping between a
// server's sections never means going back to the overview first.
//
// "Weather" rather than "Weather alerts": the rail is 208px wide and the longer
// label wraps. The page it opens still calls itself Weather alerts.
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
        <NavLink key={section.to} to={`${base}${section.to}`} end={section.end} className={({ isActive }) => `pill ${isActive ? 'active' : ''}`}>
          {section.label}
        </NavLink>
      ))}
    </nav>
  )
}

// Wraps a guild sub-page's body so it sits beside the section nav. Pages keep
// rendering their own header and content; this only provides the two-column frame.
export function GuildShell({ guildId, children }: { guildId?: string; children: ReactNode }) {
  // Same query key and staleTime as GuildOverview, so arriving from the overview
  // costs nothing and the rail names the server on every sub-page.
  const guild = useQuery({
    queryKey: ['guild', guildId],
    queryFn: () => api<GuildOverview>(`/guilds/${guildId}`),
    enabled: !!guildId,
  })
  // One nav, not two. The rail comes first in the DOM, so at phone width the shell
  // simply stacks and the nav lands above the content as a pill row -- no duplicate
  // landmark for a screen reader to read out, and no second copy to keep in step.
  return (
    <div className="guild-shell">
      <aside className="guild-rail">
        {guild.data && (
          <div className="guild-rail-head">
            <GuildIcon name={guild.data.name} iconUrl={guild.data.icon_url} />
            <b>{guild.data.name}</b>
          </div>
        )}
        <GuildNav guildId={guildId} />
      </aside>
      <div className="guild-main">{children}</div>
    </div>
  )
}
