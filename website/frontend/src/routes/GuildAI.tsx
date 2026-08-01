import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { AIConversation, AIUsage, Persona } from '../types/api'
import { ErrorNote } from '../components/ErrorNote'
import { GuildShell } from '../components/GuildNav'
import { BackLink, GlassSurface, IconButton, LargeTitleHeader, ListGroup, ListRow, PressableButton, SectionLabel, Sheet, Skeleton } from '../components/ios'

export function GuildAI() {
  const { guildId = '' } = useParams()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [purging, setPurging] = useState<string | null>(null)

  const personas = useQuery({ queryKey: ['ai-personas', guildId], queryFn: () => api<{ personas: Persona[] }>(`/guilds/${guildId}/ai/personas`) })
  const memories = useQuery({ queryKey: ['ai-memory', guildId], queryFn: () => api<{ conversations: AIConversation[] }>(`/guilds/${guildId}/ai/memory`) })
  const usage = useQuery({ queryKey: ['ai-usage', guildId], queryFn: () => api<AIUsage>(`/guilds/${guildId}/ai/usage`), refetchInterval: 10000 })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['ai-personas', guildId] })
    queryClient.invalidateQueries({ queryKey: ['ai-memory', guildId] })
  }
  const save = useMutation({ mutationFn: () => api<Persona>(`/guilds/${guildId}/ai/personas`, { method: 'POST', body: { name, system_prompt: prompt } }), onSuccess: () => { setName(''); setPrompt(''); refresh() } })
  const setDefault = useMutation({ mutationFn: (id: number) => api<Persona>(`/guilds/${guildId}/ai/personas/${id}/default`, { method: 'POST' }), onSuccess: refresh })
  const remove = useMutation({ mutationFn: (id: number) => api<void>(`/guilds/${guildId}/ai/personas/${id}`, { method: 'DELETE' }), onSuccess: refresh })
  const purge = useMutation({ mutationFn: (id: string) => api<void>(`/guilds/${guildId}/ai/memory/${id}`, { method: 'DELETE' }), onSuccess: () => { setPurging(null); refresh() } })

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
      </div>}
    </GlassSurface>

    <SectionLabel>Personas</SectionLabel>
    <ListGroup>
      {personas.data?.personas.length
        ? personas.data.personas.map(persona => <ListRow key={persona.id} label={persona.name} detail={persona.is_default ? 'Default persona' : 'Available'} className="strong-row">
          <span className="row-actions">
            <PressableButton className="small soft" onClick={() => setDefault.mutate(persona.id)} disabled={persona.is_default}>Default</PressableButton>
            <IconButton variant="danger" size={30} label={`Delete ${persona.name}`} onClick={() => remove.mutate(persona.id)}>×</IconButton>
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

    <SectionLabel>Channel memory</SectionLabel>
    <ListGroup>
      {memories.data?.conversations.length
        ? memories.data.conversations.map(memory => <ListRow key={memory.channel_id} label={`Channel ${memory.channel_id}`} detail={`${memory.message_count} messages · ${memory.token_count.toLocaleString()} tokens`} className="strong-row">
          <span className="row-actions">
            <PressableButton variant="danger" className="small" onClick={() => setPurging(memory.channel_id)}>Purge</PressableButton>
          </span>
        </ListRow>)
        : <ListRow label="Nothing retained yet" detail="Memory appears once somebody talks to Zephyr in a channel." />}
    </ListGroup>

    {purge.error && <ErrorNote error={purge.error} onRetry={() => purge.reset()} />}

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

    <BackLink to={`/g/${guildId}`}>Back to the server</BackLink>
  </GuildShell></main>
}
