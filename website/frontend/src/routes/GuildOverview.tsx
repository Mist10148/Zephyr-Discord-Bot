import { Fragment } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { GuildOverview as Overview } from '../types/api'
import { GuildIcon } from '../components/DiscordAvatar'
import { ErrorNote } from '../components/ErrorNote'
import { GuildShell } from '../components/GuildNav'
import { useGuildMeta } from '../lib/player'
import { BackLink, GlassSurface, LargeTitleHeader, ListGroup, ListRow, SectionLabel, Skeleton, WidgetGrid } from '../components/ios'

// A raw id, but marked as one. DESIGN.md allows the fallback only when the
// lookup is unavailable, and it has to read as a fallback -- a bare 19-digit
// number is indistinguishable from a value somebody chose.
function RawId({ id }: { id: string }) {
  return <span className="mono faint" title="Zephyr is not reachable, so this could not be named">{id}</span>
}

function presence(botPresent: boolean | null) {
  if (botPresent === null) return { text: 'Unknown', tone: 'unknown' }
  return botPresent ? { text: 'In this server', tone: 'ok' } : { text: 'Not in this server', tone: 'off' }
}

const SECTIONS = [
  { path: 'music', label: 'Music', detail: 'Now playing, queue and playlists' },
  { path: 'weather-alerts', label: 'Weather alerts', detail: 'Daily digests and severe-weather watches' },
  { path: 'ai', label: 'AI', detail: 'Personas, usage, and channel memory' },
  { path: 'settings', label: 'Settings', detail: 'Prefix, DJ role, music channels' },
  { path: 'audit', label: 'Audit log', detail: 'Who changed what, from where' },
]

export function GuildOverview() {
  const { guildId } = useParams()
  // Flat array key with the interpolated dependency, and `enabled` for the
  // dependent fetch -- the convention the weather page already uses.
  // No refetchInterval: guild settings are static. Polling belongs to the player
  // snapshot, not here.
  const guild = useQuery({ queryKey: ['guild', guildId], queryFn: () => api<Overview>(`/guilds/${guildId}`), enabled: !!guildId })
  // Both are best-effort name lookups, declared above the early returns so the
  // hook order is stable. useGuildMeta already carries retry:false and a 5min
  // staleTime; /commands is unauthenticated and ETagged, and its `categories`
  // are the same cog-key -> title map /commands renders.
  const meta = useGuildMeta(guildId)
  const commands = useQuery({
    queryKey: ['commands'],
    queryFn: () => api<{ categories: { key: string; title: string }[] }>('/commands'),
    staleTime: 10 * 60_000,
    retry: false,
  })

  if (guild.isPending) return <main className="app"><Skeleton lines={6} /></main>
  // 403 and 404 arrive with a message from the API envelope, so no per-status
  // branching is needed -- and deliberately no redirect to /login, because 403 is
  // not 401 and being signed in but lacking access is a different situation.
  if (guild.error || !guild.data) return <main className="app"><LargeTitleHeader title="No access" /><ErrorNote error={guild.error} onRetry={() => guild.refetch()} /><BackLink to="/g">All servers</BackLink></main>

  const g = guild.data
  const bot = presence(g.bot_present)
  // Same query and staleTime the settings pickers use, so arriving from there
  // costs nothing. retry:false and a {} fallback are its own defaults: an
  // unreachable bot must degrade to ids, not fail the page.
  const roleName = (id: string) => meta.data?.roles.find(role => role.id === id)?.name
  const channelName = (id: string) => meta.data?.channels.find(channel => channel.id === id)?.name
  const cogTitle = (key: string) => commands.data?.categories.find(category => category.key === key)?.title

  const facts: Array<{ k: string; v: ReactNode }> = [
    { k: 'Locale', v: g.locale },
    { k: 'Timezone', v: g.timezone },
    { k: 'Default volume', v: `${g.default_volume}%` },
    { k: 'DJ role', v: !g.dj_role_id ? 'Not set' : roleName(g.dj_role_id) ?? <RawId id={g.dj_role_id} /> },
    {
      k: 'Music channels',
      // The separator is its own node rather than concatenated into the name,
      // so each channel stays one addressable piece of text.
      v: !g.music_channel_ids.length ? 'Any channel' : g.music_channel_ids.map((id, index) => (
        <Fragment key={id}>
          {index > 0 && <span aria-hidden>, </span>}
          {channelName(id) ? <span>#{channelName(id)}</span> : <RawId id={id} />}
        </Fragment>
      )),
    },
    { k: 'Enabled modules', v: g.enabled_cogs.map(key => cogTitle(key) ?? key).join(' · ') },
    { k: 'Your role', v: g.owner ? 'Owner' : 'Manager' },
  ]

  return <main className="app"><GuildShell guildId={guildId}>
    <header className="guild-head">
      <GuildIcon name={g.name} iconUrl={g.icon_url} large />
      <div>
        <h1>{g.name}</h1>
        <p className="subtitle">{g.owner ? 'You own this server' : 'You manage this server'}</p>
      </div>
    </header>

    <WidgetGrid className="overview-stats">
      <GlassSurface><h2>Prefix</h2><p className="stat-value mono">{g.prefix}</p></GlassSurface>
      <GlassSurface><h2>Zephyr</h2><p className="stat-row"><i className={`dot lg ${bot.tone}`} aria-hidden />{bot.text}</p></GlassSurface>
    </WidgetGrid>

    <SectionLabel>Configuration</SectionLabel>
    <ListGroup>
      {facts.map(fact => <ListRow key={fact.k} label={fact.k}><span className="row-value">{fact.v}</span></ListRow>)}
    </ListGroup>

    <SectionLabel>Sections</SectionLabel>
    <ListGroup>
      {SECTIONS.map(section => <ListRow key={section.path} to={`/g/${g.id}/${section.path}`} label={section.label} detail={section.detail} className="strong-row" />)}
    </ListGroup>

    {/* Only rendered when there is something to say. The old version emitted an
        empty glass card whenever both caveats were false. */}
    {(g.defaults_applied || g.bot_present === false) && <GlassSurface tier="thin">
      {g.defaults_applied && <p className="muted">This server has not been configured yet, so these are Zephyr's defaults.</p>}
      {g.bot_present === false && <p className="muted">Zephyr is not in this server, so these settings are not doing anything yet.</p>}
    </GlassSurface>}

    <BackLink to="/g">All servers</BackLink>
  </GuildShell></main>
}
