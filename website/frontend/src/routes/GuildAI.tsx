import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { AIConversation, AIUsage, Persona } from '../types/api'
import { ErrorNote } from '../components/ErrorNote'
import { LargeTitleHeader, ListGroup, ListRow, PressableButton, Skeleton } from '../components/ios'

export function GuildAI() {
  const { guildId = '' } = useParams(); const queryClient = useQueryClient()
  const [name, setName] = useState(''); const [prompt, setPrompt] = useState(''); const [selected, setSelected] = useState<string | null>(null)
  const personas = useQuery({ queryKey:['ai-personas',guildId], queryFn: () => api<{personas: Persona[]}>(`/guilds/${guildId}/ai/personas`) })
  const memories = useQuery({ queryKey:['ai-memory',guildId], queryFn: () => api<{conversations: AIConversation[]}>(`/guilds/${guildId}/ai/memory`) })
  const usage = useQuery({ queryKey:['ai-usage',guildId], queryFn: () => api<AIUsage>(`/guilds/${guildId}/ai/usage`), refetchInterval: 10000 })
  const refresh = () => { queryClient.invalidateQueries({queryKey:['ai-personas',guildId]}); queryClient.invalidateQueries({queryKey:['ai-memory',guildId]}) }
  const save = useMutation({ mutationFn: () => api<Persona>(`/guilds/${guildId}/ai/personas`, {method:'POST', body:{name,system_prompt:prompt}}), onSuccess: () => { setName(''); setPrompt(''); refresh() } })
  const setDefault = useMutation({ mutationFn: (id:number) => api<Persona>(`/guilds/${guildId}/ai/personas/${id}/default`, {method:'POST'}), onSuccess: refresh })
  const remove = useMutation({ mutationFn: (id:number) => api<void>(`/guilds/${guildId}/ai/personas/${id}`, {method:'DELETE'}), onSuccess: refresh })
  const purge = useMutation({ mutationFn: (id:string) => api<void>(`/guilds/${guildId}/ai/memory/${id}`, {method:'DELETE'}), onSuccess: () => { setSelected(null); refresh() } })
  if (personas.isPending || memories.isPending) return <main className="app"><Skeleton lines={7}/></main>
  if (personas.error || memories.error) return <main className="app"><LargeTitleHeader title="AI"/><ErrorNote error={personas.error ?? memories.error} onRetry={refresh}/></main>
  return <main className="app"><LargeTitleHeader title="AI"/><p className="muted">Only messages sent to Zephyr and its replies are retained as channel memory.</p>
    <h2>Usage</h2><ListGroup><ListRow label={usage.data?.model ?? 'Loading quota'} detail={usage.data ? `${usage.data.rpm} RPM · ${usage.data.tpm.toLocaleString()} TPM · ${usage.data.rpd} today` : undefined}/></ListGroup>
    <h2>Personas</h2><ListGroup>{personas.data?.personas.map(persona => <ListRow key={persona.id} label={persona.name} detail={persona.is_default ? 'Default persona' : 'Available'}><PressableButton variant="secondary" onClick={() => setDefault.mutate(persona.id)} disabled={persona.is_default}>Default</PressableButton><PressableButton variant="danger" onClick={() => remove.mutate(persona.id)}>Delete</PressableButton></ListRow>)}</ListGroup>
    <form className="stack" onSubmit={event => { event.preventDefault(); save.mutate() }}><input required value={name} maxLength={64} onChange={e=>setName(e.target.value)} placeholder="Persona name"/><textarea required value={prompt} maxLength={4000} onChange={e=>setPrompt(e.target.value)} placeholder="System prompt"/><PressableButton disabled={save.isPending}>Add persona</PressableButton></form>
    <h2>Channel memory</h2><ListGroup>{memories.data?.conversations.map(memory => <ListRow key={memory.channel_id} label={`Channel ${memory.channel_id}`} detail={`${memory.message_count} messages · ${memory.token_count.toLocaleString()} tokens`}><PressableButton variant="danger" onClick={() => setSelected(memory.channel_id)}>Purge</PressableButton></ListRow>)}</ListGroup>
    {selected && <div className="stack"><p>Delete all Zephyr exchanges saved for channel {selected}? This cannot be undone.</p><PressableButton variant="danger" onClick={() => purge.mutate(selected)}>Confirm purge</PressableButton><PressableButton variant="secondary" onClick={() => setSelected(null)}>Cancel</PressableButton></div>}
    <p><Link to={`/g/${guildId}`}>Back to server</Link></p></main>
}
