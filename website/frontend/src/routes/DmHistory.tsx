import { useDeferredValue, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { AIHistoryConversation, AIHistoryDetail, AIHistoryMessage, AIMessageRevision } from '../types/api'
import { ErrorNote } from '../components/ErrorNote'
import { ConfirmSheet } from '../components/ConfirmSheet'
import { BackLink, GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, SectionLabel, Sheet, Skeleton } from '../components/ios'

export function DmHistory() {
  const client = useQueryClient()
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<AIHistoryConversation | null>(null)
  const [purging, setPurging] = useState<string | null>(null)
  const deferredSearch = useDeferredValue(search)
  const history = useQuery({
    queryKey: ['dm-history', deferredSearch],
    queryFn: () => api<{ entries: AIHistoryConversation[]; next_cursor: number | null }>(`/me/ai/history${deferredSearch.trim() ? `?q=${encodeURIComponent(deferredSearch.trim())}` : ''}`),
  })
  const purge = useMutation({
    mutationFn: (channelId: string) => api<void>(`/me/ai/history/${channelId}`, { method: 'DELETE' }),
    onSuccess: () => { setPurging(null); setSelected(null); client.invalidateQueries({ queryKey: ['dm-history'] }) },
  })

  if (history.isPending) return <main className="app"><Skeleton lines={6} /></main>
  if (history.error) return <main className="app"><LargeTitleHeader title="My AI history" /><ErrorNote error={history.error} onRetry={() => history.refetch()} /></main>

  return <main className="app"><div className="page-narrow">
    <LargeTitleHeader title="My AI history" subtitle="Your private conversations with Zephyr." note="Only you can access these conversations. Server managers cannot see your DM history." />
    <GlassSurface tier="thin" className="form-card"><label className="field"><span>Search retained messages</span><input className="text-input full" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search your DM history" /></label><p className="muted">Compaction may remove older messages while keeping a summary.</p></GlassSurface>
    <SectionLabel>Conversations</SectionLabel>
    <ListGroup>{history.data?.entries.length
      ? history.data.entries.map(conversation => <ListRow key={conversation.channel_id} label={`DM conversation ${conversation.channel_id}`} detail={`${conversation.message_count} retained messages · ${conversation.token_count.toLocaleString()} tokens`}><span className="row-actions"><PressableButton variant="secondary" className="small" onClick={() => setSelected(conversation)}>View</PressableButton><PressableButton variant="danger" className="small" onClick={() => setPurging(conversation.channel_id)}>Purge</PressableButton></span></ListRow>)
      : <ListRow label={search ? 'No matching conversations' : 'No retained DM history'} detail="Your conversations with Zephyr will appear here." />}</ListGroup>
    {purge.error && <ErrorNote error={purge.error} onRetry={() => purge.reset()} />}
    <ConfirmSheet open={purging !== null} onOpenChange={open => !open && setPurging(null)} title="Purge DM history" description="Delete this private conversation from the database and Zephyr's working memory? This cannot be undone." confirmLabel="Purge conversation" pending={purge.isPending} onConfirm={() => purging && purge.mutate(purging)} />
    <Sheet open={selected !== null} onOpenChange={open => !open && setSelected(null)} label="Private DM transcript">{selected && <DmTranscript conversation={selected} onPurge={() => setPurging(selected.channel_id)} />}</Sheet>
    <BackLink to="/g">Back to servers</BackLink>
  </div></main>
}

function DmTranscript({ conversation, onPurge }: { conversation: AIHistoryConversation; onPurge(): void }) {
  const [editingId, setEditingId] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [revisionId, setRevisionId] = useState<number | null>(null)
  const [redacting, setRedacting] = useState<number | null>(null)
  const detail = useQuery({ queryKey: ['dm-history-detail', conversation.channel_id], queryFn: () => api<AIHistoryDetail>(`/me/ai/history/${conversation.channel_id}`) })
  const revisions = useQuery({ queryKey: ['dm-history-revisions', conversation.channel_id, revisionId], queryFn: () => api<{ revisions: AIMessageRevision[] }>(`/me/ai/history/${conversation.channel_id}/messages/${revisionId}/revisions`), enabled: revisionId !== null })
  const update = useMutation({ mutationFn: ({ id, content, version }: { id: number; content: string; version: number }) => api<AIHistoryMessage>(`/me/ai/history/${conversation.channel_id}/messages/${id}`, { method: 'PATCH', body: { content, expected_version: version, reason: 'Personal dashboard edit' } }), onSuccess: () => { setEditingId(null); detail.refetch() } })
  const redact = useMutation({ mutationFn: (id: number) => api<AIHistoryMessage>(`/me/ai/history/${conversation.channel_id}/messages/${id}/redact`, { method: 'POST', body: { reason: 'Personal dashboard redaction' } }), onSuccess: () => { setRedacting(null); detail.refetch() } })

  if (detail.isPending) return <Skeleton lines={5} />
  if (detail.error) return <ErrorNote error={detail.error} onRetry={() => detail.refetch()} />
  return <><h2>Private DM</h2>{detail.data?.rolling_summary && <p className="muted">Summary of compacted messages: {detail.data.rolling_summary}</p>}<div className="row-actions"><PressableButton variant="danger" className="small" onClick={onPurge}>Purge conversation</PressableButton></div>{detail.data?.messages.map(message => <article className="transcript-message" key={message.id}><b>{message.role}</b>{editingId === message.id ? <><textarea className="text-input full" rows={4} value={draft} onChange={event => setDraft(event.target.value)} /><div className="sheet-actions"><PressableButton variant="secondary" onClick={() => setEditingId(null)}>Cancel</PressableButton><PressableButton disabled={!draft.trim() || update.isPending} onClick={() => update.mutate({ id: message.id, content: draft, version: message.version })}>Save edit</PressableButton></div></> : <p>{message.content}</p>}<small>{message.tokens} tokens {message.edited_at ? ' · Edited' : ''} {message.created_at ? `· ${new Date(message.created_at).toLocaleString()}` : ''}</small>{editingId !== message.id && <div className="row-actions"><PressableButton variant="secondary" className="small" onClick={() => { setEditingId(message.id); setDraft(message.content) }}>Edit</PressableButton><PressableButton variant="secondary" className="small" onClick={() => setRevisionId(message.id)}>Revisions</PressableButton>{!message.redacted_at && <PressableButton variant="danger" className="small" onClick={() => setRedacting(message.id)}>Redact</PressableButton>}</div>}{revisionId === message.id && <div className="muted">{revisions.data?.revisions.map(revision => <p key={revision.id}>{revision.previous_content} → {revision.replacement_content}</p>)}</div>}</article>)}<ConfirmSheet open={redacting !== null} onOpenChange={open => !open && setRedacting(null)} title="Redact DM message" description="Replace this message with a redaction marker? Its revision remains recorded." confirmLabel="Redact message" pending={redact.isPending} onConfirm={() => redacting !== null && redact.mutate(redacting)} /></>
}
