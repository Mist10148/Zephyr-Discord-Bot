import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { GuildOverview as Overview } from '../types/api'
import { GuildIcon } from '../components/DiscordAvatar'
import { ErrorNote } from '../components/ErrorNote'
import { GlassSurface, LargeTitleHeader, ListGroup, ListRow, Skeleton, WidgetGrid } from '../components/ios'

function botLabel(botPresent: boolean | null) {
  if (botPresent === null) return 'Unknown'
  return botPresent ? 'In this server' : 'Not in this server'
}

export function GuildOverview() {
  const { guildId } = useParams()
  // Flat array key with the interpolated dependency, and `enabled` for the
  // dependent fetch -- the convention the weather page already uses.
  // No refetchInterval: guild settings are static. Polling belongs to the player
  // snapshot, not here.
  const guild = useQuery({ queryKey: ['guild', guildId], queryFn: () => api<Overview>(`/guilds/${guildId}`), enabled: !!guildId })

  if (guild.isPending) return <main className="app"><Skeleton lines={6} /></main>
  // 403 and 404 arrive with a message from the API envelope, so no per-status
  // branching is needed -- and deliberately no redirect to /login, because 403 is
  // not 401 and being signed in but lacking access is a different situation.
  if (guild.error || !guild.data) return <main className="app"><LargeTitleHeader title="No access" /><ErrorNote error={guild.error} onRetry={() => guild.refetch()} /><p><Link to="/g">All servers</Link></p></main>

  const g = guild.data
  return <main className="app">
    <header className="large-title" style={{ display: 'flex', alignItems: 'center', gap: '.75rem' }}>
      <GuildIcon name={g.name} iconUrl={g.icon_url} large />
      <h1>{g.name}</h1>
    </header>

    <WidgetGrid>
      <GlassSurface><h2>Prefix</h2><p>{g.prefix}</p></GlassSurface>
      <GlassSurface><h2>Zephyr</h2><p>{botLabel(g.bot_present)}</p></GlassSurface>
    </WidgetGrid>

    <ListGroup>
      <ListRow label="Locale" detail={g.locale} />
      <ListRow label="Timezone" detail={g.timezone} />
      <ListRow label="Default volume" detail={`${g.default_volume}%`} />
      <ListRow label="DJ role" detail={g.dj_role_id ?? 'Not set'} />
      <ListRow label="Music channels" detail={g.music_channel_ids.length ? g.music_channel_ids.join(', ') : 'Any channel'} />
      <ListRow label="Enabled modules" detail={g.enabled_cogs.join(', ')} />
      <ListRow label="Your role" detail={g.owner ? 'Owner' : 'Manager'} />
    </ListGroup>

    <ListGroup>
      <ListRow to={`/g/${g.id}/music`} label="Music" detail="Now playing, queue and playlists" />
      <ListRow to={`/g/${g.id}/weather-alerts`} label="Weather alerts" detail="Daily digests and severe-weather watches" />
      <ListRow to={`/g/${g.id}/ai`} label="AI" detail="Personas, usage, and channel memory" />
      <ListRow to={`/g/${g.id}/settings`} label="Settings" detail="Prefix, DJ role, music channels" />
      <ListRow to={`/g/${g.id}/audit`} label="Audit log" detail="Who changed what, from where" />
    </ListGroup>

    <GlassSurface>
      {g.defaults_applied && <p className="muted">This server has not been configured yet, so these are Zephyr's defaults.</p>}
      {g.bot_present === false && <p className="muted">Zephyr is not in this server, so these settings are not doing anything yet.</p>}
    </GlassSurface>

    <p><Link to="/g">All servers</Link></p>
  </main>
}
