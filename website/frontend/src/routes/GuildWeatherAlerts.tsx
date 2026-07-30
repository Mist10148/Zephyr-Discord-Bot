import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { haptic } from '../lib/haptics'
import { useGuildMeta } from '../lib/player'
import { ErrorNote } from '../components/ErrorNote'
import { CapsuleToast, GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, Sheet, Skeleton, Toggle } from '../components/ios'
import type { SubKind, SubPreview, WeatherSub, WeatherSubList } from '../types/api'

const KIND_HELP: Record<SubKind, string> = {
  daily: 'A forecast posted once a day at a time you choose.',
  severe: 'Posted only when wind, rain, heat or a storm crosses a threshold.',
  class_suspension: 'Posted when the heat index reaches an advisory level. Advisory only — always confirm with your school.',
}

function describe(sub: WeatherSub) {
  const when = sub.schedule_local_time ? ` at ${sub.schedule_local_time} (${sub.tz})` : ''
  return `${sub.location} → #${sub.channel_id}${when}${sub.enabled ? '' : ' • paused'}`
}

function PreviewSheet({ guildId, sub, onClose }: { guildId: string | undefined; sub: WeatherSub; onClose(): void }) {
  const preview = useQuery({
    queryKey: ['weather-sub-preview', sub.id],
    queryFn: () => api<SubPreview>(`/guilds/${guildId}/weather-subs/${sub.id}/preview`),
  })

  return <div className="stack">
    <h2>Preview — {sub.location}</h2>
    {preview.isPending && <Skeleton lines={4} />}
    {preview.error && <ErrorNote error={preview.error} onRetry={() => preview.refetch()} />}
    {preview.data && (preview.data.alert
      ? <GlassSurface>
        <h3>{preview.data.alert.title}</h3>
        <p>{preview.data.alert.summary}</p>
        <ListGroup>{preview.data.alert.fields.map(field => <ListRow key={field.name} label={field.name} detail={field.value} />)}</ListGroup>
        {/* Shown because it explains a genuinely confusing case: the preview has
            something to say, and the channel will still see nothing. */}
        {preview.data.duplicate && <p className="muted">This is the same alert that was posted last time, so it would not be sent again until the forecast changes.</p>}
      </GlassSurface>
      : <GlassSurface><p>Nothing to report right now.</p><p className="muted">This subscription would stay quiet — which is what a watch does most of the time.</p></GlassSurface>)}
    <PressableButton variant="secondary" onClick={onClose}>Close</PressableButton>
  </div>
}

function CreateSheet({ guildId, kinds, onDone }: { guildId: string | undefined; kinds: SubKind[]; onDone(): void }) {
  const client = useQueryClient()
  const meta = useGuildMeta(guildId)
  const [kind, setKind] = useState<SubKind>('daily')
  const [location, setLocation] = useState('')
  const [channelId, setChannelId] = useState('')
  const [at, setAt] = useState('08:00')
  const [tz, setTz] = useState('')

  const create = useMutation({
    mutationFn: () => api<WeatherSub>(`/guilds/${guildId}/weather-subs`, {
      method: 'POST',
      body: {
        kind, location, channel_id: channelId,
        // A daily digest needs a time and the others must not carry one; the API
        // refuses both mistakes, so the form should not make them.
        ...(kind === 'daily' ? { schedule_local_time: at } : {}),
        ...(tz ? { tz } : {}),
      },
    }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['weather-subs', guildId] }); onDone() },
  })

  const postable = meta.data?.channels.filter(channel => channel.can_send) ?? []
  return <div className="stack">
    <h2>New subscription</h2>
    <ListGroup>
      <ListRow label="What to post">
        <select className="text-input" value={kind} onChange={event => setKind(event.target.value as SubKind)}>
          {kinds.map(value => <option key={value} value={value}>{value === 'daily' ? 'Daily digest' : value === 'severe' ? 'Severe weather watch' : 'Class suspension watch'}</option>)}
        </select>
      </ListRow>
      <ListRow label="Place">
        <input className="text-input" value={location} placeholder="Iloilo City" onChange={event => setLocation(event.target.value)} />
      </ListRow>
      <ListRow label="Channel">
        {/* Channels that Zephyr cannot post in are left out rather than shown and
            rejected later: a subscription pointed at one fails silently forever. */}
        {meta.data
          ? <select className="text-input" value={channelId} onChange={event => setChannelId(event.target.value)}>
            <option value="">Choose a channel</option>
            {postable.map(channel => <option key={channel.id} value={channel.id}>#{channel.name}</option>)}
          </select>
          : <input className="text-input" value={channelId} placeholder="Channel id" onChange={event => setChannelId(event.target.value)} />}
      </ListRow>
      {kind === 'daily' && <ListRow label="Time" detail="Local to the timezone below">
        <input className="text-input short" value={at} placeholder="08:00" onChange={event => setAt(event.target.value)} />
      </ListRow>}
      <ListRow label="Timezone" detail="Leave empty to use the place's own">
        <input className="text-input" value={tz} placeholder="Asia/Manila" onChange={event => setTz(event.target.value)} />
      </ListRow>
    </ListGroup>
    <p className="muted">{KIND_HELP[kind]}</p>
    {!meta.data && !meta.isPending && <p className="muted">Zephyr is not reachable, so its channels cannot be listed. You can still paste a channel id.</p>}
    {create.error && <ErrorNote error={create.error} onRetry={() => create.reset()} />}
    <div className="transport">
      <PressableButton disabled={!location.trim() || !channelId.trim() || create.isPending} onClick={() => { haptic(15); create.mutate() }}>{create.isPending ? 'Saving…' : 'Subscribe'}</PressableButton>
      <PressableButton variant="secondary" onClick={onDone}>Cancel</PressableButton>
    </div>
  </div>
}

export function GuildWeatherAlerts() {
  const { guildId } = useParams()
  const client = useQueryClient()
  const subs = useQuery({ queryKey: ['weather-subs', guildId], queryFn: () => api<WeatherSubList>(`/guilds/${guildId}/weather-subs`), enabled: !!guildId })
  const [creating, setCreating] = useState(false)
  const [previewing, setPreviewing] = useState<WeatherSub | null>(null)

  const invalidate = () => client.invalidateQueries({ queryKey: ['weather-subs', guildId] })
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api(`/guilds/${guildId}/weather-subs/${id}`, { method: 'PATCH', body: { enabled } }),
    onSuccess: invalidate,
  })
  const remove = useMutation({
    mutationFn: (id: number) => api(`/guilds/${guildId}/weather-subs/${id}`, { method: 'DELETE' }),
    onSuccess: invalidate,
  })

  if (subs.isPending) return <main className="app"><Skeleton lines={6} /></main>
  if (subs.error || !subs.data) {
    return <main className="app">
      <LargeTitleHeader title="Weather alerts" />
      <ErrorNote error={subs.error} onRetry={() => subs.refetch()} />
      <p><Link to={`/g/${guildId}`}>Back to the server</Link></p>
    </main>
  }

  return <main className="app">
    <LargeTitleHeader title="Weather alerts" />

    {subs.data.subscriptions.length === 0
      ? <GlassSurface>
        <p>No weather is being posted in this server yet.</p>
        <p className="muted">A daily digest arrives at a time you pick. The two watches stay quiet until there is something worth saying.</p>
      </GlassSurface>
      : <ListGroup>
        {subs.data.subscriptions.map(sub => <ListRow key={sub.id} label={sub.kind_label} detail={describe(sub)}>
          <span className="row-actions">
            <Toggle checked={sub.enabled} onChange={enabled => { haptic(15); toggle.mutate({ id: sub.id, enabled }) }} />
            <PressableButton variant="secondary" onClick={() => setPreviewing(sub)}>Preview</PressableButton>
            <PressableButton variant="danger" onClick={() => { haptic(15); remove.mutate(sub.id) }}>Delete</PressableButton>
          </span>
        </ListRow>)}
      </ListGroup>}

    {toggle.error && <ErrorNote error={toggle.error} onRetry={() => toggle.reset()} />}
    {remove.error && <ErrorNote error={remove.error} onRetry={() => remove.reset()} />}
    {/* Pausing is not deleting, and the difference is worth stating: a paused
        subscription keeps its place and its thresholds. */}
    {subs.data.subscriptions.some(sub => !sub.enabled) && <CapsuleToast>Paused subscriptions keep their settings.</CapsuleToast>}

    <PressableButton onClick={() => setCreating(true)}>Add a subscription</PressableButton>

    <Sheet open={creating} onOpenChange={setCreating}>
      <CreateSheet guildId={guildId} kinds={subs.data.kinds} onDone={() => setCreating(false)} />
    </Sheet>
    <Sheet open={previewing !== null} onOpenChange={open => !open && setPreviewing(null)}>
      {previewing && <PreviewSheet guildId={guildId} sub={previewing} onClose={() => setPreviewing(null)} />}
    </Sheet>

    <p><Link to={`/g/${guildId}`}>Back to the server</Link></p>
  </main>
}
