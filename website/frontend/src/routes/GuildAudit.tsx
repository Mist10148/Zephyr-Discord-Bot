import { useInfiniteQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { AuditEntry, AuditPage } from '../types/api'
import { ErrorNote } from '../components/ErrorNote'
import { GuildShell } from '../components/GuildNav'
import { GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, Skeleton } from '../components/ios'

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
}

function actionLabel(action: string) {
  if (ACTION_LABELS[action]) return ACTION_LABELS[action]
  if (action.startsWith('player.')) return `Player: ${action.slice(7)}`
  return action
}

// The stored timestamp is UTC ISO; render it in the viewer's own locale/zone rather
// than pretending to know theirs.
function when(iso: string | null) {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const source = entry.source === 'web' ? 'Dashboard' : entry.source === 'discord' ? 'Discord' : entry.source
  return <ListRow label={actionLabel(entry.action)} detail={`${when(entry.created_at)} • ${source}`} />
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
  if (query.error) return <main className="app"><LargeTitleHeader title="Audit log" /><ErrorNote error={query.error} onRetry={() => query.refetch()} /><p><Link to={`/g/${guildId}`}>Back</Link></p></main>

  const entries = query.data.pages.flatMap(page => page.entries)
  return <main className="app"><GuildShell guildId={guildId}>
    <LargeTitleHeader title="Audit log" subtitle="Who changed what in this server, and from where." />
    {entries.length === 0
      ? <GlassSurface><p className="muted">Nothing has been changed here yet. Settings edits and player actions from the dashboard show up here.</p></GlassSurface>
      : <ListGroup>{entries.map(entry => <AuditRow key={entry.id} entry={entry} />)}</ListGroup>}
    {query.hasNextPage && <div className="transport">
      <PressableButton variant="secondary" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
        {query.isFetchingNextPage ? 'Loading…' : 'Load older'}
      </PressableButton>
    </div>}
  </GuildShell></main>
}
