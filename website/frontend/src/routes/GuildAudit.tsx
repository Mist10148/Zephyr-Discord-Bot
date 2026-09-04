import { useInfiniteQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { groupByDay, timeOfDay } from '../lib/audit-groups'
import type { AuditActor, AuditEntry, AuditPage } from '../types/api'
import { ErrorNote } from '../components/ErrorNote'
import { GuildShell } from '../components/GuildNav'
import { BackLink, GlassSurface, LargeTitleHeader, ListGroup, PressableButton, SectionLabel, Skeleton } from '../components/ios'

// Actions are recorded as dotted machine strings (`player.skip`, `ai.persona.create`);
// this is the one place they are shown to a human, so give them a readable label
// rather than leaking the wire format into the UI.
const ACTION_LABELS: Record<string, string> = {
  'settings.update': 'Updated server settings',
  'ai.persona.create': 'Created an AI persona',
  'ai.persona.update': 'Edited an AI persona',
  'ai.persona.delete': 'Deleted an AI persona',
  'ai.persona.default': 'Changed the default persona',
  'ai.memory.purge': 'Purged channel memory',
  'weather_sub.create': 'Added a weather subscription',
  'weather_sub.update': 'Edited a weather subscription',
  'weather_sub.delete': 'Removed a weather subscription',
}

function actionLabel(action: string) {
  if (ACTION_LABELS[action]) return ACTION_LABELS[action]
  if (action.startsWith('player.')) return `Player: ${action.slice(7)}`
  return action
}

function sourceLabel(source: string) {
  return source === 'web' ? 'Dashboard' : source === 'discord' ? 'Discord' : source
}

function AuditRow({ entry, actor }: { entry: AuditEntry; actor?: AuditActor }) {
  const source = sourceLabel(entry.source)
  return <div className="list-row">
    <span className="audit-time">{timeOfDay(entry.created_at)}</span>
    {/* The name when the bot could supply one, the id when it could not -- and
        the fallback reads as a fallback rather than as a value, so nobody
        mistakes a 19-digit number for something meaningful. */}
    <span className="row-label">{actionLabel(entry.action)}<small>{actor ? actor.name : <span className="mono faint">{entry.actor_id}</span>}</small></span>
    {/* Dashboard is accent-tinted and everything else neutral: the question this
        page exists to answer is "was that us, or was it done in Discord?" */}
    <span className={`badge ${source === 'Dashboard' ? 'accent' : ''}`.trim()}>{source}</span>
    {entry.payload != null && <details className="audit-details"><summary>Details</summary><pre>{typeof entry.payload === 'string' ? entry.payload : JSON.stringify(entry.payload, null, 2)}</pre></details>}
  </div>
}

// Prefixes rather than whole action names: the log holds `player.volume`,
// `player.skip`, `settings.update`, `weather_sub.create`, `ai.memory.purge` and
// more, and the question is nearly always about a family. The server matches
// `action` as a prefix for exactly this reason.
const ACTION_FILTERS = [
  { value: '', label: 'All actions' },
  { value: 'player', label: 'Player' },
  { value: 'settings', label: 'Settings' },
  { value: 'weather_sub', label: 'Weather alerts' },
  { value: 'ai', label: 'AI' },
  { value: 'playlist', label: 'Playlists' },
]

const SOURCE_FILTERS = [
  { value: '', label: 'Anywhere' },
  { value: 'web', label: 'Dashboard' },
  { value: 'discord', label: 'Discord' },
]

export function GuildAudit() {
  const { guildId } = useParams()
  const [action, setAction] = useState('')
  const [source, setSource] = useState('')

  // The filters are part of the query key, so changing one starts a fresh
  // keyset walk rather than appending filtered pages to unfiltered ones.
  const query = useInfiniteQuery({
    queryKey: ['audit', guildId, action, source],
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams()
      if (pageParam) params.set('before', String(pageParam))
      if (action) params.set('action', action)
      if (source) params.set('source', source)
      const search = params.toString()
      return api<AuditPage>(`/guilds/${guildId}/audit${search ? `?${search}` : ''}`)
    },
    enabled: !!guildId,
    initialPageParam: 0 as number,
    // 0 is "the first page" (no cursor); null from the server means no older rows.
    getNextPageParam: last => last.next_before ?? undefined,
    // Changing a filter changes the query key, which would otherwise put the
    // page back into isPending -- blanking the list *and unmounting the select
    // that was just used*. Keeping the previous pages means the rows dim and
    // swap instead.
    placeholderData: previous => previous,
  })

  const filtering = !!action || !!source
  const filters = (
    <div className="audit-filters">
      <label className="field inline"><span>Action</span>
        <select className="text-input inline" value={action} onChange={event => setAction(event.target.value)}>
          {ACTION_FILTERS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </label>
      <label className="field inline"><span>From</span>
        <select className="text-input inline" value={source} onChange={event => setSource(event.target.value)}>
          {SOURCE_FILTERS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </label>
      {filtering && <PressableButton className="small" variant="secondary" onClick={() => { setAction(''); setSource('') }}>Clear</PressableButton>}
    </div>
  )

  if (query.isPending) return <main className="app"><GuildShell guildId={guildId}><LargeTitleHeader title="Audit log" subtitle="Who changed what in this server, and from where." />{filters}<Skeleton variant="rows" count={6} /></GuildShell></main>
  if (query.error) return <main className="app"><LargeTitleHeader title="Audit log" /><ErrorNote error={query.error} onRetry={() => query.refetch()} /><BackLink to={`/g/${guildId}`}>Back to the server</BackLink></main>

  const entries = query.data.pages.flatMap(page => page.entries)
  // Merged across pages: each page resolves only its own actors, and the
  // same person appears on several.
  const actors = Object.assign({}, ...query.data.pages.map(page => page.actors ?? {}))
  // Grouping happens over the flattened list, not per page, so a day that
  // straddles a pagination boundary stays one group rather than two.
  const groups = groupByDay(entries)

  return <main className="app"><GuildShell guildId={guildId}>
    <LargeTitleHeader title="Audit log" subtitle="Who changed what in this server, and from where." />
    {filters}
    {groups.length === 0
      ? <GlassSurface tier="thin"><p className="muted">{filtering
        // An empty filtered page and an empty log look identical otherwise, and
        // one of them means "change the filter".
        ? 'Nothing matches those filters. Try widening them.'
        : 'Nothing has been changed here yet. Settings edits and player actions from the dashboard show up here.'}</p></GlassSurface>
      : groups.map(group => <div className="audit-group" key={group.key}>
        <SectionLabel>{group.date}</SectionLabel>
        <ListGroup>{group.entries.map(entry => <AuditRow key={entry.id} entry={entry} actor={actors[entry.actor_id]} />)}</ListGroup>
      </div>)}
    {query.hasNextPage && <div className="actions">
      <PressableButton variant="secondary" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
        {query.isFetchingNextPage ? 'Loading…' : 'Load older'}
      </PressableButton>
    </div>}
    <BackLink to={`/g/${guildId}`}>Back to the server</BackLink>
  </GuildShell></main>
}
