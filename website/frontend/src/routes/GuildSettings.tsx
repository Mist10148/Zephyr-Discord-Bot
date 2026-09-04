import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { haptic } from '../lib/haptics'
import { useGuildMeta } from '../lib/player'
import { ErrorNote } from '../components/ErrorNote'
import { GuildShell } from '../components/GuildNav'
import { BackLink, GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, SectionLabel, Skeleton, Slider } from '../components/ios'
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

function FieldError({ children }: { children: string }) {
  return <p className="field-error" role="alert"><i className="toast-badge" aria-hidden>!</i>{children}</p>
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
    meta: { success: 'Settings saved' },
    mutationFn: (patch: Partial<Draft>) => api<Settings>(`/guilds/${guildId}/settings`, { method: 'PATCH', body: patch }),
    onSuccess: data => {
      client.setQueryData(['guild-settings', guildId], data)
      client.invalidateQueries({ queryKey: ['guild', guildId] })
      setSaved(true)
    },
  })

  if (settings.isPending || !draft) return <main className="app"><Skeleton variant="rows" count={6} /></main>
  if (settings.error) return <main className="app"><LargeTitleHeader title="Settings" /><ErrorNote error={settings.error} onRetry={() => settings.refetch()} /><BackLink to={`/g/${guildId}`}>Back to the server</BackLink></main>

  const original = draftOf(settings.data!)
  const patch = changed(draft, original)
  const dirty = Object.keys(patch).length > 0
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => { setSaved(false); setDraft({ ...draft, [key]: value }) }
  const invalidField = save.error instanceof ApiError && save.error.code === 'invalid_value'
    ? (save.error.detail as { field?: string } | null)?.field
    : undefined

  return <main className="app"><GuildShell guildId={guildId}>
    <LargeTitleHeader title="Settings" subtitle="Prefix, DJ role, music channels and more." />
    {settings.data!.defaults_applied && <GlassSurface tier="thin"><p className="muted">This server has never been configured, so these are Zephyr's defaults. Saving stores them.</p></GlassSurface>}

    <ListGroup>
      {/* Each invalid field states the rule and flags the control itself. The old
          version swapped the row's detail text and left the input looking fine,
          which is invisible to anyone not re-reading the whole row. */}
      <ListRow label="Prefix" detail="1–5 characters">
        <span className="row-actions">
          <input className={`text-input inline w-prefix ${invalidField === 'prefix' ? 'invalid' : ''}`.trim()} aria-invalid={invalidField === 'prefix'} aria-label="Prefix" value={draft.prefix} onChange={event => set('prefix', event.target.value)} />
        </span>
        {invalidField === 'prefix' && <FieldError>Not accepted — use 1 to 5 characters.</FieldError>}
      </ListRow>
      <ListRow label="Locale">
        <span className="row-actions">
          <input className="text-input inline w-locale" aria-label="Locale" value={draft.locale} onChange={event => set('locale', event.target.value)} />
        </span>
      </ListRow>
      <ListRow label="Timezone" detail="e.g. Asia/Manila">
        <span className="row-actions">
          <input className={`text-input inline w-tz ${invalidField === 'timezone' ? 'invalid' : ''}`.trim()} aria-invalid={invalidField === 'timezone'} aria-label="Timezone" value={draft.timezone} onChange={event => set('timezone', event.target.value)} />
        </span>
        {invalidField === 'timezone' && <FieldError>Not an IANA name — use Region/City.</FieldError>}
      </ListRow>
      <ListRow label="Default volume">
        <span className="row-actions">
          <span className="row-value mono">{draft.default_volume}%</span>
          <Slider label="Default volume" value={draft.default_volume} onChange={value => set('default_volume', value)} />
        </span>
      </ListRow>
      <ListRow label="DJ role" detail="Everyone can control the player until one is set">
        <span className="row-actions">
          {meta.data
            ? <select className="text-input inline" aria-label="DJ role" value={draft.dj_role_id ?? ''} onChange={event => set('dj_role_id', event.target.value || null)}>
              <option value="">No DJ role</option>
              {meta.data.roles.map(role => <option key={role.id} value={role.id}>{role.name}</option>)}
            </select>
            : <input className="text-input inline w-tz" aria-label="DJ role id" value={draft.dj_role_id ?? ''} placeholder="Role id" onChange={event => set('dj_role_id', event.target.value || null)} />}
        </span>
      </ListRow>
    </ListGroup>

    <SectionLabel>Music channels</SectionLabel>
    {meta.data
      ? <ListGroup>
        {meta.data.channels.map(channel => {
          const checked = draft.music_channel_ids.includes(channel.id)
          return <ListRow
            key={channel.id}
            label={`#${channel.name}`}
            detail={channel.can_send ? undefined : 'Zephyr cannot post here'}
            leading={<span className={`checkbox ${checked ? 'on' : ''}`.trim()} aria-hidden>✓</span>}
            pressed={checked}
            onClick={() => set('music_channel_ids', checked
              ? draft.music_channel_ids.filter(id => id !== channel.id)
              : [...draft.music_channel_ids, channel.id])}
          />
        })}
      </ListGroup>
      : <GlassSurface tier="thin"><p className="muted">Zephyr is not reachable, so its channels cannot be listed. Music commands are allowed in every channel while this is unset.</p></GlassSurface>}

    

    {/* Sticky: on a form this long the actions used to scroll out of reach, so a
        change made at the bottom meant hunting for the button that saves it. */}
    <div className="save-bar" data-glass="1">
      {saved && !dirty && <span className="saved" role="status"><i aria-hidden>✓</i>Saved</span>}
      <span className="spacer" />
      <PressableButton variant="secondary" className="small" disabled={!dirty} onClick={() => { setDraft(original); setSaved(false) }}>Discard</PressableButton>
      <PressableButton className="small" disabled={!dirty || save.isPending} onClick={() => { haptic(15); save.mutate(patch) }}>{save.isPending ? 'Saving…' : 'Save changes'}</PressableButton>
    </div>

    <BackLink to={`/g/${guildId}`}>Back to the server</BackLink>
  </GuildShell></main>
}
