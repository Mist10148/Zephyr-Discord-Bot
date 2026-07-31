import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { haptic } from '../lib/haptics'
import { useGuildMeta } from '../lib/player'
import { ErrorNote } from '../components/ErrorNote'
import { CapsuleToast, GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, Skeleton, Slider } from '../components/ios'
import type { GuildSettings as Settings } from '../types/api'

type Draft = Pick<Settings, 'prefix' | 'locale' | 'timezone' | 'default_volume' | 'dj_role_id' | 'music_channel_ids'>

function draftOf(settings: Settings): Draft {
  return {
    prefix: settings.prefix, locale: settings.locale, timezone: settings.timezone,
    default_volume: settings.default_volume, dj_role_id: settings.dj_role_id,
    music_channel_ids: settings.music_channel_ids,
  }
}

// Only what changed is sent. A PATCH of every field would rewrite values the user
// never touched, and would make two people editing different settings overwrite
// each other for no reason.
function changed(draft: Draft, original: Draft) {
  const patch: Partial<Draft> = {}
  for (const key of Object.keys(draft) as (keyof Draft)[]) {
    if (JSON.stringify(draft[key]) !== JSON.stringify(original[key])) {
      (patch as Record<string, unknown>)[key] = draft[key]
    }
  }
  return patch
}

export function GuildSettings() {
  const { guildId } = useParams()
  const client = useQueryClient()
  const settings = useQuery({ queryKey: ['guild-settings', guildId], queryFn: () => api<Settings>(`/guilds/${guildId}/settings`), enabled: !!guildId })
  // The bot has to be reachable to name a channel or a role. When it is not, the
  // pickers degrade to plain id fields rather than the page failing.
  const meta = useGuildMeta(guildId)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => { setDraft(settings.data ? draftOf(settings.data) : null) }, [settings.data])

  const save = useMutation({
    mutationFn: (patch: Partial<Draft>) => api<Settings>(`/guilds/${guildId}/settings`, { method: 'PATCH', body: patch }),
    onSuccess: data => {
      client.setQueryData(['guild-settings', guildId], data)
      client.invalidateQueries({ queryKey: ['guild', guildId] })
      setSaved(true)
    },
  })

  if (settings.isPending || !draft) return <main className="app"><Skeleton lines={6} /></main>
  if (settings.error) return <main className="app"><LargeTitleHeader title="Settings" /><ErrorNote error={settings.error} onRetry={() => settings.refetch()} /><p><Link to={`/g/${guildId}`}>Back</Link></p></main>

  const original = draftOf(settings.data!)
  const patch = changed(draft, original)
  const dirty = Object.keys(patch).length > 0
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => { setSaved(false); setDraft({ ...draft, [key]: value }) }
  const invalidField = save.error instanceof ApiError && save.error.code === 'invalid_value'
    ? (save.error.detail as { field?: string } | null)?.field
    : undefined

  return <main className="app">
    <LargeTitleHeader title="Settings" />
    {settings.data!.defaults_applied && <GlassSurface><p className="muted">This server has never been configured, so these are Zephyr's defaults. Saving stores them.</p></GlassSurface>}

    <ListGroup>
      <ListRow label="Prefix" detail={invalidField === 'prefix' ? 'Not accepted' : '1–5 characters'}>
        <input className="text-input short" value={draft.prefix} onChange={event => set('prefix', event.target.value)} />
      </ListRow>
      <ListRow label="Locale">
        <input className="text-input short" value={draft.locale} onChange={event => set('locale', event.target.value)} />
      </ListRow>
      <ListRow label="Timezone" detail={invalidField === 'timezone' ? 'Not an IANA name' : 'e.g. Asia/Manila'}>
        <input className="text-input" value={draft.timezone} onChange={event => set('timezone', event.target.value)} />
      </ListRow>
      <ListRow label="Default volume" detail={`${draft.default_volume}%`}>
        <Slider label="Default volume" value={draft.default_volume} onChange={value => set('default_volume', value)} />
      </ListRow>
      <ListRow label="DJ role" detail="Everyone can control the player until one is set">
        {meta.data
          ? <select className="text-input" value={draft.dj_role_id ?? ''} onChange={event => set('dj_role_id', event.target.value || null)}>
            <option value="">No DJ role</option>
            {meta.data.roles.map(role => <option key={role.id} value={role.id}>{role.name}</option>)}
          </select>
          : <input className="text-input" value={draft.dj_role_id ?? ''} placeholder="Role id" onChange={event => set('dj_role_id', event.target.value || null)} />}
      </ListRow>
    </ListGroup>

    <h2>Music channels</h2>
    {meta.data
      ? <ListGroup>
        {meta.data.channels.map(channel => <ListRow key={channel.id} label={`#${channel.name}`} detail={channel.can_send ? undefined : 'Zephyr cannot post here'}>
          <input
            type="checkbox"
            checked={draft.music_channel_ids.includes(channel.id)}
            onChange={event => set('music_channel_ids', event.target.checked
              ? [...draft.music_channel_ids, channel.id]
              : draft.music_channel_ids.filter(id => id !== channel.id))}
          />
        </ListRow>)}
      </ListGroup>
      : <GlassSurface><p className="muted">Zephyr is not reachable, so its channels cannot be listed. Music commands are allowed in every channel while this is unset.</p></GlassSurface>}

    {save.error && <ErrorNote error={save.error} onRetry={() => save.reset()} />}
    {saved && !dirty && <CapsuleToast>Saved</CapsuleToast>}

    <div className="transport">
      <PressableButton disabled={!dirty || save.isPending} onClick={() => { haptic(15); save.mutate(patch) }}>{save.isPending ? 'Saving…' : 'Save changes'}</PressableButton>
      <PressableButton variant="secondary" disabled={!dirty} onClick={() => { setDraft(original); setSaved(false) }}>Discard</PressableButton>
    </div>

    <p><Link to={`/g/${guildId}`}>Back to the server</Link></p>
  </main>
}
