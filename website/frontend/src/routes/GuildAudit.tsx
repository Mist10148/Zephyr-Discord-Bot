import { useInfiniteQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { groupByDay, timeOfDay } from '../lib/audit-groups'
import type { AuditEntry, AuditPage } from '../types/api'
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

function AuditRow({ entry }: { entry: AuditEntry }) {
  const source = sourceLabel(entry.source)
  return <div className="list-row">
    <span className="audit-time">{timeOfDay(entry.created_at)}</span>
    <span className="row-label">{actionLabel(entry.action)}</span>
    {/* Dashboard is accent-tinted and everything else neutral: the question this
        page exists to answer is "was that us, or was it done in Discord?" */}
    <span className={`badge ${source === 'Dashboard' ? 'accent' : ''}`.trim()}>{source}</span>
  </div>
}

export function GuildAudit() {
  const { guildId } = useParams()
  const query = useInfiniteQuery({
    queryKey: ['audit', guildId],
    queryFn: ({ pageParam }) => api<AuditPage>(`/guilds/${guildId}/audit${pageParam ? `?before=${pageParam}` : ''}`),
    enabled: !!guildId,
    initialPageParam: 0 as number,
    // 0 is "the first page" (no cursor); null from the server means no older rows.
    getNextPageParam: last => last.next_before ?? undefined,
  })

  if (query.isPending) return <main className="app"><Skeleton lines={6} /></main>
  if (query.error) return <main className="app"><LargeTitleHeader title="Audit log" /><ErrorNote error={query.error} onRetry={() => query.refetch()} /><BackLink to={`/g/${guildId}`}>Back to the server</BackLink></main>

  const entries = query.data.pages.flatMap(page => page.entries)
  // Grouping happens over the flattened list, not per page, so a day that
  // straddles a pagination boundary stays one group rather than two.
  const groups = groupByDay(entries)

  return <main className="app"><GuildShell guildId={guildId}>
    <LargeTitleHeader title="Audit log" subtitle="Who changed what in this server, and from where." />
    {groups.length === 0
      ? <GlassSurface tier="thin"><p className="muted">Nothing has been changed here yet. Settings edits and player actions from the dashboard show up here.</p></GlassSurface>
      : groups.map(group => <div className="audit-group" key={group.key}>
        <SectionLabel>{group.date}</SectionLabel>
        <ListGroup>{group.entries.map(entry => <AuditRow key={entry.id} entry={entry} />)}</ListGroup>
      </div>)}
    {query.hasNextPage && <div className="actions">
      <PressableButton variant="secondary" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
        {query.isFetchingNextPage ? 'Loading…' : 'Load older'}
      </PressableButton>
    </div>}
    <BackLink to={`/g/${guildId}`}>Back to the server</BackLink>
  </GuildShell></main>
}
