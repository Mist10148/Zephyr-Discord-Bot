import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { haptic } from '../lib/haptics'
import { useGuildMeta } from '../lib/player'
import { ErrorNote } from '../components/ErrorNote'
import { GuildShell } from '../components/GuildNav'
import { ConfirmSheet } from '../components/ConfirmSheet'
import { BackLink, CapsuleToast, GlassSurface, IconButton, LargeTitleHeader, ListGroup, ListRow, PressableButton, SegmentedControl, Sheet, Skeleton, Toggle } from '../components/ios'
import type { SubKind, SubPreview, WeatherSub, WeatherSubList } from '../types/api'

const KIND_HELP: Record<SubKind, string> = {
  daily: 'A forecast posted once a day at a time you choose.',
  severe: 'Posted only when wind, rain, heat or a storm crosses a threshold.',
  class_suspension: 'Posted when the heat index reaches an advisory level. Advisory only — always confirm with your school.',
}
const KIND_LABELS: Record<SubKind, string> = {
  daily: 'Daily digest',
  severe: 'Severe weather watch',
  class_suspension: 'Class suspension watch',
}

function describe(sub: WeatherSub) {
  const when = sub.schedule_local_time ? ` at ${sub.schedule_local_time} (${sub.tz})` : ` (${sub.tz})`
  return `${sub.location} → #${sub.channel_id}${when}${sub.enabled ? '' : ' · paused'}${sub.last_run_at ? ` · Last posted ${new Date(sub.last_run_at).toLocaleString()}` : ''}`
}

function PreviewSheet({ guildId, sub, onClose }: { guildId: string | undefined; sub: WeatherSub; onClose(): void }) {
  const preview = useQuery({
    queryKey: ['weather-sub-preview', sub.id],
    queryFn: () => api<SubPreview>(`/guilds/${guildId}/weather-subs/${sub.id}/preview`),
  })

  return <>
    <h2>Preview — {sub.location}</h2>
    {preview.isPending && <Skeleton lines={4} />}
    {preview.error && <ErrorNote error={preview.error} onRetry={() => preview.refetch()} />}
    {preview.data && (preview.data.alert
      ? <>
        {/* Shaped like the Discord embed it would become, so "what would be
            posted" is answered by looking rather than by reading a description. */}
        <div className="embed">
          <div className="embed-head"><i className="dot danger" aria-hidden /><b>{preview.data.alert.title}</b></div>
          <p>{preview.data.alert.summary}</p>
          {preview.data.alert.fields.length > 0 && <div className="embed-fields">
            {preview.data.alert.fields.map(field => <div key={field.name}><span>{field.name}</span><b>{field.value}</b></div>)}
          </div>}
        </div>
        {/* Shown because it explains a genuinely confusing case: the preview has
            something to say, and the channel will still see nothing. */}
        {preview.data.duplicate && <p className="muted small-note">This is the same alert that was posted last time, so it would not be sent again until the forecast changes.</p>}
      </>
      : <GlassSurface tier="thin"><p>Nothing to report right now.</p><p className="muted">This subscription would stay quiet — which is what a watch does most of the time.</p></GlassSurface>)}
    <div className="sheet-actions"><PressableButton variant="secondary" onClick={onClose}>Close</PressableButton></div>
  </>
}

function CreateSheet({ guildId, kinds, onDone }: { guildId: string | undefined; kinds: SubKind[]; onDone(): void }) {
  const client = useQueryClient()
  const meta = useGuildMeta(guildId)
  const [kind, setKind] = useState<SubKind>('daily')
  const [location, setLocation] = useState('')
  const [channelId, setChannelId] = useState('')
  const [at, setAt] = useState('08:00')
  const [tz, setTz] = useState('')
  const [units, setUnits] = useState<'metric' | 'imperial'>('metric')

  const create = useMutation({
    meta: { success: 'Subscription created' },
    mutationFn: () => api<WeatherSub>(`/guilds/${guildId}/weather-subs`, {
      method: 'POST',
      body: {
        kind, location, channel_id: channelId,
        // A daily digest needs a time and the others must not carry one; the API
        // refuses both mistakes, so the form should not make them.
        ...(kind === 'daily' ? { schedule_local_time: at } : {}),
        ...(tz ? { tz } : {}), units,
      },
    }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['weather-subs', guildId] }); onDone() },
  })

  const postable = meta.data?.channels.filter(channel => channel.can_send) ?? []
  return <>
    <h2>New subscription</h2>
    <p>Choose what to post, where, and when.</p>
    <div className="stack">
      <label className="field">
        <span>What to post</span>
        {/* A segmented control rather than a select: three fixed options where the
            choice changes the rest of the form, so it should be visible at a glance. */}
        <SegmentedControl values={kinds} labels={KIND_LABELS} value={kind} onChange={value => setKind(value as SubKind)} />
      </label>
      <label className="field">
        <span>Place</span>
        <input className="text-input full" value={location} placeholder="Iloilo City" onChange={event => setLocation(event.target.value)} />
      </label>
      <label className="field">
        <span>Channel</span>
        {/* Channels that Zephyr cannot post in are left out rather than shown and
            rejected later: a subscription pointed at one fails silently forever. */}
        {meta.data
          ? <select className="text-input full" value={channelId} onChange={event => setChannelId(event.target.value)}>
            <option value="">Choose a channel</option>
            {postable.map(channel => <option key={channel.id} value={channel.id}>#{channel.name}</option>)}
          </select>
          : <input className="text-input full" value={channelId} placeholder="Channel id" onChange={event => setChannelId(event.target.value)} />}
      </label>
      <div className="field-row">
        {kind === 'daily' && <label className="field" style={{ flex: 1 }}>
          <span>Time</span>
          <input className="text-input full mono" value={at} placeholder="08:00" onChange={event => setAt(event.target.value)} />
        </label>}
        <label className="field" style={{ flex: 1.4 }}>
          <span>Timezone</span>
          <input className="text-input full" value={tz} placeholder="Asia/Manila" onChange={event => setTz(event.target.value)} />
        </label>
      </div>
      <label className="field"><span>Units</span><SegmentedControl values={['metric', 'imperial']} labels={{ metric: 'Metric', imperial: 'Imperial' }} value={units} onChange={value => setUnits(value as 'metric' | 'imperial')} /></label>
      <p className="note">{KIND_HELP[kind]}</p>
      {!tz && <p className="muted small-note">Leave the timezone empty to use the place's own.</p>}
      {!meta.data && !meta.isPending && <p className="muted small-note">Zephyr is not reachable, so its channels cannot be listed. You can still paste a channel id.</p>}
      
    </div>
    <div className="sheet-actions">
      <PressableButton variant="secondary" onClick={onDone}>Cancel</PressableButton>
      <PressableButton disabled={!location.trim() || !channelId.trim() || create.isPending} onClick={() => { haptic(15); create.mutate() }}>{create.isPending ? 'Saving…' : 'Subscribe'}</PressableButton>
    </div>
  </>
}

export function GuildWeatherAlerts() {
  const { guildId } = useParams()
  const client = useQueryClient()
  const subs = useQuery({ queryKey: ['weather-subs', guildId], queryFn: () => api<WeatherSubList>(`/guilds/${guildId}/weather-subs`), enabled: !!guildId })
  const [creating, setCreating] = useState(false)
  const [previewing, setPreviewing] = useState<WeatherSub | null>(null)
  const [deleting, setDeleting] = useState<WeatherSub | null>(null)

  const invalidate = () => client.invalidateQueries({ queryKey: ['weather-subs', guildId] })
  const toggle = useMutation({
    meta: { success: 'Subscription updated' },
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api(`/guilds/${guildId}/weather-subs/${id}`, { method: 'PATCH', body: { enabled } }),
    onMutate: async ({ id, enabled }) => { await client.cancelQueries({ queryKey: ['weather-subs', guildId] }); const previous = client.getQueryData<WeatherSubList>(['weather-subs', guildId]); if (previous) client.setQueryData<WeatherSubList>(['weather-subs', guildId], { ...previous, subscriptions: previous.subscriptions.map(sub => sub.id === id ? { ...sub, enabled } : sub) }); return { previous } },
    onError: (_error, _values, context) => { if (context?.previous) client.setQueryData(['weather-subs', guildId], context.previous) }, onSettled: invalidate,
  })
  const remove = useMutation({
    meta: { success: 'Subscription removed' },
    mutationFn: (id: number) => api(`/guilds/${guildId}/weather-subs/${id}`, { method: 'DELETE' }),
    onSuccess: invalidate,
  })

  if (subs.isPending) return <main className="app"><Skeleton lines={6} /></main>
  if (subs.error || !subs.data) {
    return <main className="app">
      <LargeTitleHeader title="Weather alerts" />
      <ErrorNote error={subs.error} onRetry={() => subs.refetch()} />
      <BackLink to={`/g/${guildId}`}>Back to the server</BackLink>
    </main>
  }

  return <main className="app"><GuildShell guildId={guildId}>
    <LargeTitleHeader title="Weather alerts" subtitle="Daily digests and severe-weather watches posted to your channels." />

    {subs.data.subscriptions.length === 0
      ? <GlassSurface>
        <p>No weather is being posted in this server yet.</p>
        <p className="muted">A daily digest arrives at a time you pick. The two watches stay quiet until there is something worth saying.</p>
      </GlassSurface>
      : <ListGroup>
        {subs.data.subscriptions.map(sub => <ListRow key={sub.id} label={sub.kind_label} detail={describe(sub)} className="strong-row">
          <span className="row-actions">
            <Toggle label={`${sub.kind_label} enabled`} checked={sub.enabled} onChange={enabled => { haptic(15); toggle.mutate({ id: sub.id, enabled }) }} />
            <PressableButton variant="secondary" className="small" onClick={() => setPreviewing(sub)}>Preview</PressableButton>
            <IconButton variant="danger" size={30} label={`Delete ${sub.kind_label} for ${sub.location}`} onClick={() => setDeleting(sub)}>×</IconButton>
          </span>
        </ListRow>)}
      </ListGroup>}

    
    
    {/* Pausing is not deleting, and the difference is worth stating: a paused
        subscription keeps its place and its thresholds. */}
    {subs.data.subscriptions.some(sub => !sub.enabled) && <CapsuleToast>Paused subscriptions keep their settings.</CapsuleToast>}

    <div className="actions"><PressableButton onClick={() => setCreating(true)}>Add a subscription</PressableButton></div>

    <Sheet open={creating} onOpenChange={setCreating} label="New subscription">
      <CreateSheet guildId={guildId} kinds={subs.data.kinds} onDone={() => setCreating(false)} />
    </Sheet>
    <Sheet open={previewing !== null} onOpenChange={open => !open && setPreviewing(null)} label="Subscription preview">
      {previewing && <PreviewSheet guildId={guildId} sub={previewing} onClose={() => setPreviewing(null)} />}
    </Sheet>
    <ConfirmSheet open={deleting !== null} onOpenChange={open => !open && setDeleting(null)} title="Delete weather subscription" description={`Stop and remove the ${deleting?.kind_label ?? ''} for ${deleting?.location ?? ''}?`} confirmLabel="Delete subscription" pending={remove.isPending} onConfirm={() => deleting && remove.mutate(deleting.id, { onSuccess: () => setDeleting(null) })} />

    <BackLink to={`/g/${guildId}`}>Back to the server</BackLink>
  </GuildShell></main>
}
