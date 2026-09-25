import { useDeferredValue, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { AIHistoryConversation, AIHistoryDetail, AIHistoryMessage, AIMessageRevision, AIUsage, Persona } from '../types/api'
import { ErrorNote } from '../components/ErrorNote'
import { GuildShell } from '../components/GuildNav'
import { ConfirmSheet } from '../components/ConfirmSheet'
import { BackLink, GlassSurface, IconButton, LargeTitleHeader, ListGroup, ListRow, PressableButton, SectionLabel, Sheet, Skeleton } from '../components/ios'

export function GuildAI() {
  const { guildId = '' } = useParams()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [purging, setPurging] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<Persona | null>(null)
  const [editing, setEditing] = useState<Persona | null>(null)
  const [transcript, setTranscript] = useState<AIHistoryConversation | null>(null)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')
  const [archived, setArchived] = useState('active')
  const deferredSearch = useDeferredValue(search)

  const personas = useQuery({ queryKey: ['ai-personas', guildId], queryFn: () => api<{ personas: Persona[] }>(`/guilds/${guildId}/ai/personas`) })
  const memories = useQuery({
    queryKey: ['ai-history', guildId, deferredSearch, category, archived],
    queryFn: () => {
      const params = new URLSearchParams()
      if (deferredSearch.trim()) params.set('q', deferredSearch.trim())
      if (category) params.set('category', category)
      if (archived !== 'all') params.set('archived', archived === 'archived' ? 'true' : 'false')
      const query = params.toString()
      return api<{ entries: AIHistoryConversation[]; next_cursor: number | null }>(`/guilds/${guildId}/ai/history${query ? `?${query}` : ''}`)
    },
  })
  const usage = useQuery({ queryKey: ['ai-usage', guildId], queryFn: () => api<AIUsage>(`/guilds/${guildId}/ai/usage`), refetchInterval: 10000 })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['ai-personas', guildId] })
    queryClient.invalidateQueries({ queryKey: ['ai-history', guildId] })
  }
  const save = useMutation({ mutationFn: () => api<Persona>(`/guilds/${guildId}/ai/personas`, { method: 'POST', body: { name, system_prompt: prompt } }), onSuccess: () => { setName(''); setPrompt(''); refresh() } })
  const setDefault = useMutation({ mutationFn: (id: number) => api<Persona>(`/guilds/${guildId}/ai/personas/${id}/default`, { method: 'POST' }), onSuccess: refresh })
  const remove = useMutation({ mutationFn: (id: number) => api<void>(`/guilds/${guildId}/ai/personas/${id}`, { method: 'DELETE' }), onSuccess: refresh })
  const purge = useMutation({ mutationFn: (id: string) => api<void>(`/guilds/${guildId}/ai/memory/${id}`, { method: 'DELETE' }), onSuccess: () => { setPurging(null); refresh() } })
  const updateHistory = useMutation({
    mutationFn: ({ channelId, body }: { channelId: string; body: { category?: string; is_archived?: boolean } }) => api<AIHistoryConversation>(`/guilds/${guildId}/ai/history/${channelId}`, { method: 'PATCH', body }),
    onSuccess: refresh,
  })

  if (personas.isPending || memories.isPending) return <main className="app"><Skeleton lines={7} /></main>
  if (personas.error || memories.error) return <main className="app"><LargeTitleHeader title="AI" /><ErrorNote error={personas.error ?? memories.error} onRetry={refresh} /><BackLink to={`/g/${guildId}`}>Back to the server</BackLink></main>

  return <main className="app"><GuildShell guildId={guildId}>
    <LargeTitleHeader
      title="AI"
      subtitle="Personas, quota and channel memory."
      note="Only messages sent to Zephyr and its replies are retained as channel memory."
    />

    <GlassSurface className="quota">
      <div className="model">
        <span className="section-label">Model</span>
        <b>{usage.data?.model ?? 'Loading quota…'}</b>
      </div>
      {usage.data && <div className="figures">
        <span>{usage.data.rpm} RPM</span>
        <span>{usage.data.tpm.toLocaleString()} TPM</span>
        <span>{usage.data.rpd} today</span>
        <span>{usage.data.totals.total_tokens.toLocaleString()} tokens total</span>
        <span>{usage.data.totals.successful_requests} successful requests</span>
      </div>}
    </GlassSurface>
    {usage.data?.cooldown_until && <GlassSurface tier="thin" className="notice"><p>AI requests are cooling down until {new Date(usage.data.cooldown_until).toLocaleString()}.</p></GlassSurface>}

    <SectionLabel>Personas</SectionLabel>
    <ListGroup>
      {personas.data?.personas.length
        ? personas.data.personas.map(persona => <ListRow key={persona.id} label={persona.name} detail={persona.is_default ? 'Default persona' : 'Available'} className="strong-row">
          <span className="row-actions">
            <PressableButton className="small soft" onClick={() => setDefault.mutate(persona.id)} disabled={persona.is_default}>Default</PressableButton>
            <PressableButton variant="secondary" className="small" onClick={() => setEditing(persona)}>Edit</PressableButton><IconButton variant="danger" size={30} label={`Delete ${persona.name}`} onClick={() => setDeleting(persona)}>×</IconButton>
          </span>
        </ListRow>)
        : <ListRow label="No personas yet" detail="Zephyr will answer in its own voice until you add one." />}
    </ListGroup>

    <GlassSurface tier="thin" className="form-card">
      <form className="stack" onSubmit={event => { event.preventDefault(); save.mutate() }}>
        <label className="field">
          <span>Name</span>
          <input className="text-input full" required value={name} maxLength={64} onChange={event => setName(event.target.value)} placeholder="Weather nerd" />
        </label>
        <label className="field">
          <span>System prompt</span>
          <textarea className="text-input full" required rows={3} value={prompt} maxLength={4000} onChange={event => setPrompt(event.target.value)} placeholder="Answer in two sentences. Always mention the heat index." />
        </label>
        {save.error && <ErrorNote error={save.error} onRetry={() => save.reset()} />}
        <PressableButton type="submit" className="self-start" disabled={save.isPending}>{save.isPending ? 'Saving…' : 'Add persona'}</PressableButton>
      </form>
    </GlassSurface>

    <SectionLabel>AI history</SectionLabel>
    <GlassSurface tier="thin" className="form-card">
      <div className="stack">
        <label className="field">
          <span>Search retained messages</span>
          <input className="text-input full" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search conversation text" />
        </label>
        <div className="row-actions">
          <label className="field"><span>Category</span><input className="text-input" value={category} onChange={event => setCategory(event.target.value)} placeholder="Any category" /></label>
          <label className="field"><span>View</span><select className="text-input" value={archived} onChange={event => setArchived(event.target.value)}><option value="active">Active</option><option value="archived">Archived</option><option value="all">All</option></select></label>
        </div>
        <p className="muted">Only exchanges sent to Zephyr are retained. Compaction may remove older messages while keeping a summary.</p>
      </div>
    </GlassSurface>
    <ListGroup>
      {memories.data?.entries.length
        ? memories.data.entries.map(memory => <ListRow key={memory.channel_id} label={`Channel ${memory.channel_id}`} detail={`${memory.message_count} retained messages · ${memory.token_count.toLocaleString()} tokens${memory.category ? ` · ${memory.category}` : ''}${memory.is_archived ? ' · Archived' : ''}`} className="strong-row">
          <span className="row-actions">
            <PressableButton variant="secondary" className="small" onClick={() => setTranscript(memory)}>View</PressableButton>
            <PressableButton className="small soft" onClick={() => updateHistory.mutate({ channelId: memory.channel_id, body: { is_archived: !memory.is_archived } })}>{memory.is_archived ? 'Restore' : 'Archive'}</PressableButton>
            <PressableButton variant="danger" className="small" onClick={() => setPurging(memory.channel_id)}>Purge</PressableButton>
          </span>
        </ListRow>)
        : <ListRow label={search || category ? 'No matching conversations' : 'Nothing retained yet'} detail="Memory appears once somebody talks to Zephyr in a channel." />}
    </ListGroup>

    {(purge.error || updateHistory.error) && <ErrorNote error={purge.error ?? updateHistory.error} onRetry={() => { purge.reset(); updateHistory.reset() }} />}

    {/* A sheet, like every other destructive confirmation in the dashboard. This
        used to be an inline block that pushed the page around when it appeared. */}
    <Sheet open={purging !== null} onOpenChange={open => !open && setPurging(null)} label="Purge channel memory">
      <h2>Purge channel memory</h2>
      <p>Delete all Zephyr exchanges saved for channel {purging}? This cannot be undone.</p>
      <div className="sheet-actions">
        <PressableButton variant="secondary" onClick={() => setPurging(null)}>Cancel</PressableButton>
        <PressableButton variant="danger" disabled={purge.isPending} onClick={() => purging && purge.mutate(purging)}>{purge.isPending ? 'Purging…' : 'Confirm purge'}</PressableButton>
      </div>
    </Sheet>
    <ConfirmSheet open={deleting !== null} onOpenChange={open => !open && setDeleting(null)} title="Delete AI persona" description={`Delete ${deleting?.name ?? 'this persona'}? This cannot be undone.`} confirmLabel="Delete persona" pending={remove.isPending} onConfirm={() => deleting && remove.mutate(deleting.id, { onSuccess: () => setDeleting(null) })} />
    <Sheet open={editing !== null} onOpenChange={open => !open && setEditing(null)} label="Edit persona">{editing && <PersonaEditor guildId={guildId} persona={editing} onDone={() => { setEditing(null); refresh() }} />}</Sheet>
    <Sheet open={transcript !== null} onOpenChange={open => !open && setTranscript(null)} label="Channel memory transcript">{transcript && <Transcript guildId={guildId} memory={transcript} />}</Sheet>

    <BackLink to={`/g/${guildId}`}>Back to the server</BackLink>
  </GuildShell></main>
}

function PersonaEditor({ guildId, persona, onDone }: { guildId: string; persona: Persona; onDone(): void }) {
  const [name, setName] = useState(persona.name); const [prompt, setPrompt] = useState(persona.system_prompt); const update = useMutation({ mutationFn: () => api<Persona>(`/guilds/${guildId}/ai/personas/${persona.id}`, { method: 'PATCH', body: { name, system_prompt: prompt, is_default: persona.is_default } }), onSuccess: onDone })
  return <><h2>Edit persona</h2><label className="field"><span>Name</span><input className="text-input full" value={name} onChange={event => setName(event.target.value)} /></label><label className="field"><span>System prompt</span><textarea className="text-input full" rows={5} value={prompt} onChange={event => setPrompt(event.target.value)} /></label>{update.error && <ErrorNote error={update.error} onRetry={() => update.reset()} />}<div className="sheet-actions"><PressableButton disabled={!name.trim() || !prompt.trim() || update.isPending} onClick={() => update.mutate()}>{update.isPending ? 'Saving…' : 'Save persona'}</PressableButton></div></>
}
function Transcript({ guildId, memory }: { guildId: string; memory: AIHistoryConversation }) {
  const [editingId, setEditingId] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [revisionMessageId, setRevisionMessageId] = useState<number | null>(null)
  const [label, setLabel] = useState('')
  const [note, setNote] = useState('')
  const [redacting, setRedacting] = useState<number | null>(null)
  const detail = useQuery({ queryKey: ['ai-history-detail', guildId, memory.channel_id], queryFn: () => api<AIHistoryDetail>(`/guilds/${guildId}/ai/history/${memory.channel_id}`) })
  const revisions = useQuery({ queryKey: ['ai-history-revisions', guildId, revisionMessageId], queryFn: () => api<{ revisions: AIMessageRevision[] }>(`/guilds/${guildId}/ai/history/${memory.channel_id}/messages/${revisionMessageId}/revisions`), enabled: revisionMessageId !== null })
  const update = useMutation({
    mutationFn: ({ messageId, content, version }: { messageId: number; content: string; version: number }) => api<AIHistoryMessage>(`/guilds/${guildId}/ai/history/${memory.channel_id}/messages/${messageId}`, { method: 'PATCH', body: { content, expected_version: version, reason: 'Dashboard edit' } }),
    onSuccess: () => { setEditingId(null); detail.refetch() },
  })
  const addLabel = useMutation({ mutationFn: () => api(`/guilds/${guildId}/ai/history/${memory.channel_id}/labels`, { method: 'POST', body: { label } }), onSuccess: () => { setLabel(''); detail.refetch() } })
  const addAnnotation = useMutation({ mutationFn: () => api(`/guilds/${guildId}/ai/history/${memory.channel_id}/annotations`, { method: 'POST', body: { note } }), onSuccess: () => { setNote(''); detail.refetch() } })
  const removeAnnotation = useMutation({ mutationFn: (id: number) => api(`/guilds/${guildId}/ai/history/${memory.channel_id}/annotations/${id}`, { method: 'DELETE' }), onSuccess: () => detail.refetch() })
  const redact = useMutation({ mutationFn: (id: number) => api<AIHistoryMessage>(`/guilds/${guildId}/ai/history/${memory.channel_id}/messages/${id}/redact`, { method: 'POST', body: { reason: 'Dashboard redaction' } }), onSuccess: () => { setRedacting(null); detail.refetch() } })
  return <><h2>Channel {memory.channel_id}</h2>{detail.isPending && <Skeleton lines={5} />}{detail.error && <ErrorNote error={detail.error} onRetry={() => detail.refetch()} />}{detail.data?.rolling_summary && <p className="muted">Summary of compacted messages: {detail.data.rolling_summary}</p>}
  {detail.data && <>
    <div className="stack"><span className="section-label">Labels</span><div className="row-actions">{detail.data.labels.map(item => <span className="badge" key={item.id}>{item.label}</span>)}</div><div className="row-actions"><input className="text-input" value={label} onChange={event => setLabel(event.target.value)} placeholder="Add label" /><PressableButton className="small" disabled={!label.trim() || addLabel.isPending} onClick={() => addLabel.mutate()}>Add</PressableButton></div></div>
    <div className="stack"><span className="section-label">Private annotations</span>{detail.data.annotations.map(item => <div className="list-row" key={item.id}><span className="row-label">{item.note}</span><IconButton variant="danger" size={30} label="Remove annotation" onClick={() => removeAnnotation.mutate(item.id)}>×</IconButton></div>)}<textarea className="text-input full" rows={2} value={note} onChange={event => setNote(event.target.value)} placeholder="Add a private admin note" /><PressableButton className="self-start" disabled={!note.trim() || addAnnotation.isPending} onClick={() => addAnnotation.mutate()}>Add note</PressableButton></div>
  </>}
  {detail.data?.messages.map(message => <article className="transcript-message" key={message.id}>
    <b>{message.role}</b>
    {editingId === message.id ? <><textarea className="text-input full" rows={4} value={draft} onChange={event => setDraft(event.target.value)} /><div className="sheet-actions"><PressableButton variant="secondary" onClick={() => setEditingId(null)}>Cancel</PressableButton><PressableButton disabled={!draft.trim() || update.isPending} onClick={() => update.mutate({ messageId: message.id, content: draft, version: message.version })}>{update.isPending ? 'Saving…' : 'Save edit'}</PressableButton></div></> : <p>{message.content}</p>}
    <small>{message.tokens} tokens {message.edited_at ? ' · Edited' : ''} {message.created_at ? `· ${new Date(message.created_at).toLocaleString()}` : ''}</small>
    {editingId !== message.id && <div className="row-actions"><PressableButton variant="secondary" className="small" onClick={() => { setEditingId(message.id); setDraft(message.content) }}>Edit</PressableButton><PressableButton variant="secondary" className="small" onClick={() => setRevisionMessageId(message.id)}>Revisions</PressableButton>{!message.redacted_at && <PressableButton variant="danger" className="small" onClick={() => setRedacting(message.id)}>Redact</PressableButton>}</div>}
    {revisionMessageId === message.id && <div className="muted">{revisions.isPending && 'Loading revisions…'}{revisions.data?.revisions.map(revision => <p key={revision.id}>{revision.previous_content} → {revision.replacement_content}{revision.reason ? ` (${revision.reason})` : ''}</p>)}</div>}
  </article>)}
  <ConfirmSheet open={redacting !== null} onOpenChange={open => !open && setRedacting(null)} title="Redact AI message" description="Replace this retained message with a redaction marker? The original remains available in revision history." confirmLabel="Redact message" pending={redact.isPending} onConfirm={() => redacting !== null && redact.mutate(redacting)} />
  </>
}
