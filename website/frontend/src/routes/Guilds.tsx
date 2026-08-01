import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { useLogout, useMe } from '../lib/auth'
import { haptic } from '../lib/haptics'
import { GuildIcon, UserAvatar } from '../components/DiscordAvatar'
import { ErrorNote } from '../components/ErrorNote'
import { Chevron, GlassSurface, PressableButton, SectionLabel, WidgetGrid } from '../components/ios'
import type { MeGuild } from '../types/api'

type Status = { bot: { online: boolean; guild_count: number | null; latency_ms: number | null; uptime_s: number | null } }

// bot_present is a tri-state: null means the bot has never published a snapshot, so
// saying "not added" would be a guess. Servers without the bot are still listed --
// hiding a server somebody administers is an unexplainable dead end. The dot colour
// carries the same three states as the text, so the grid is scannable without
// reading every card.
function presence(botPresent: boolean | null) {
  if (botPresent === null) return { text: 'Bot status unknown', tone: 'unknown' }
  return botPresent
    ? { text: 'Zephyr is in this server', tone: 'ok' }
    : { text: 'Zephyr is not in this server yet', tone: 'off' }
}

// "3d 4h" reads better than 273600 seconds. Deliberately two units: the third is
// never the reason anyone is looking.
function formatUptime(seconds: number | null | undefined) {
  if (seconds == null) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days) return `${days}d ${hours}h`
  if (hours) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

function ServerCard({ guild }: { guild: MeGuild }) {
  const { text, tone } = presence(guild.bot_present)
  return <Link to={`/g/${guild.id}`} className="glass glass-regular glass-interactive server-card" data-glass="1">
    <GuildIcon name={guild.name} iconUrl={guild.icon_url} />
    <span className="server-card-body">
      <b>{guild.name}</b>
      <small><i className={`dot ${tone}`} aria-hidden />{text}</small>
    </span>
    <Chevron />
  </Link>
}

export function Guilds() {
  const me = useMe()
  const logout = useLogout()
  const navigate = useNavigate()
  // Public endpoint, already cached by the home page. It is what turns this from a
  // list of links into a dashboard: without it the page can say which servers you
  // manage but nothing about the bot that is supposed to be running in them.
  const status = useQuery({ queryKey: ['status'], queryFn: () => api<Status>('/status') })

  // RequireAuth has already gated this route, so data is present. The non-null
  // assertion matches the house style (see Weather's place!.latitude).
  const { user, guilds, invite_url: inviteUrl, guilds_stale: stale } = me.data!
  const bot = status.data?.bot
  const present = guilds.filter(guild => guild.bot_present === true).length
  const name = user.global_name ?? user.username

  return <main className="app">
    <header className="dash-head">
      <div>
        <h1>Dashboard</h1>
        <p className="subtitle">Welcome back, {name}.</p>
      </div>
      <div className="dash-account">
        <UserAvatar name={user.username} avatarUrl={user.avatar_url} />
        <span className="dash-identity"><b>{name}</b><small>@{user.username}</small></span>
        <PressableButton variant="danger" className="small" disabled={logout.isPending} onClick={() => { haptic(15); logout.mutate(undefined, { onSuccess: () => navigate('/login', { replace: true }) }) }}>
          {logout.isPending ? 'Signing out…' : 'Sign out'}
        </PressableButton>
      </div>
    </header>

    {logout.error && <ErrorNote error={logout.error} onRetry={() => logout.reset()} />}

    <WidgetGrid className="dash-stats">
      <GlassSurface>
        <h2>Zephyr</h2>
        {/* Three states, not two: a pending /status must not render as "offline". */}
        <p className="stat-row">
          <i className={`dot lg ${status.isPending ? '' : bot?.online ? 'ok' : 'off'}`.trim()} aria-hidden />
          {status.isPending ? 'Checking…' : bot?.online ? 'Online' : 'Offline'}
        </p>
        <small className="muted">{bot?.online && bot.latency_ms != null ? `${Math.round(bot.latency_ms)} ms` : 'No heartbeat'}</small>
      </GlassSurface>
      <GlassSurface>
        <h2>You manage</h2>
        <p className="stat-value">{guilds.length}</p>
        <small className="muted">server{guilds.length === 1 ? '' : 's'}</small>
      </GlassSurface>
      <GlassSurface>
        <h2>Zephyr is in</h2>
        <p className="stat-value">{present}<span className="stat-of"> / {guilds.length}</span></p>
        <small className="muted">of the servers you manage</small>
      </GlassSurface>
      <GlassSurface>
        <h2>Uptime</h2>
        <p className="stat-value">{formatUptime(bot?.uptime_s)}</p>
        <small className="muted">{bot?.guild_count != null ? `${bot.guild_count} servers total` : 'Not reporting'}</small>
      </GlassSurface>
    </WidgetGrid>

    {stale && <GlassSurface tier="thin" className="notice">
      <i className="dot unknown" aria-hidden />
      <p>Your server list may be out of date. <a href="/api/v1/auth/login?next=%2Fg">Refresh it</a>.</p>
    </GlassSurface>}

    <div className="section-head">
      <SectionLabel>Your servers</SectionLabel>
      <a className="ios-button secondary small" href={inviteUrl}>Add Zephyr to a server</a>
    </div>

    {guilds.length === 0
      ? <GlassSurface>
        <p>No servers yet.</p>
        <p className="muted">You can only manage servers where you have the Manage Server permission. Invite Zephyr to one, then reload this page.</p>
      </GlassSurface>
      : <div className="server-grid">{guilds.map(guild => <ServerCard key={guild.id} guild={guild} />)}</div>}
  </main>
}
