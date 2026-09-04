import { useEffect, useState } from 'react'
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors } from '@dnd-kit/core'
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useParams } from 'react-router-dom'
import { haptic } from '../lib/haptics'
import { useToast } from '../lib/toast'
import { formatDuration, useElapsed, usePlayer, usePlayerAction } from '../lib/player'
import { ConfirmSheet } from '../components/ConfirmSheet'
import { ErrorNote } from '../components/ErrorNote'
import { GuildShell } from '../components/GuildNav'
import { PlaylistPanel } from '../components/PlaylistPanel'
import { BackLink, GlassSurface, IconButton, LargeTitleHeader, ListGroup, ListRow, PressableButton, SegmentedControl, Skeleton, Slider, Toggle } from '../components/ios'
import { DiscIcon, PauseIcon, PlayIcon, ShuffleIcon, SkipIcon, StopIcon } from '../components/icons'
import type { Effects, PlayerTrack } from '../types/api'
const LOOP_MODES = ['off', 'track', 'queue']; const LOOP_LABELS = { off: 'Off', track: 'Track', queue: 'Queue' }
// Named and explained, rather than derived from the key. `effect.replace('_',
// ' ')` is non-global, so `sixteen_d` read as "sixteen d" and `slownrev` as
// "slownrev" -- and none of these is guessable from its identifier even when
// the underscore is handled, which is why each carries a line saying what it
// does to the sound.
const TOGGLE_EFFECTS = [
  { key: 'nightcore', label: 'Nightcore', detail: 'Faster, and pitched up' },
  { key: 'vaporwave', label: 'Vaporwave', detail: 'Slower, and pitched down' },
  { key: 'reverb', label: 'Reverb', detail: 'Adds room and tail' },
  { key: 'slowed', label: 'Slowed', detail: 'Slower, at the original pitch' },
  { key: 'slownrev', label: 'Slowed + Reverb', detail: 'Both at once' },
  { key: 'sixteen_d', label: '16D Audio', detail: 'Pans the track around your head' },
] as const satisfies ReadonlyArray<{ key: keyof Effects; label: string; detail: string }>
function ProgressBar({ elapsed, duration, onSeek }: { elapsed: number; duration: number; onSeek(position: number): void }) { const [draft, setDraft] = useState<number | null>(null); const value = draft ?? elapsed; return <><Slider label="Seek position" value={Math.round(value)} min={0} max={Math.max(1, Math.round(duration))} onChange={setDraft} onCommit={() => { if (draft != null) onSeek(draft); setDraft(null) }} /><p className="times"><span>{formatDuration(value)}</span><span>{formatDuration(duration)}</span></p></> }
// A continuous control that commits on release rather than streaming.
//
// Both effects sliders used to call run('effects', ...) straight from onChange.
// Dragging pitch across 0 -> 2 at step .1 issued a request per step, so the
// control felt like mud and one drag could consume most of PLAYER_RATE_LIMIT
// (30 per window, see website/api/player.py) and start returning 429s.
//
// Same draft-then-commit shape as ProgressBar above: the local draft wins while
// the pointer is down, and the mutation fires once on release. `format` exists
// because a bare number on a slider means nothing -- the volume row shows
// "50%", and these showed nothing at all.
function EffectSlider({ label, value, min, max, step, format, onCommit }: { label: string; value: number; min: number; max: number; step?: number; format(value: number): string; onCommit(value: number): void }) {
  const [draft, setDraft] = useState<number | null>(null)
  const shown = draft ?? value
  return <ListRow label={label}>
    <span className="row-actions">
      <span className="row-value mono">{format(shown)}</span>
      <Slider label={label} value={shown} min={min} max={max} step={step} onChange={setDraft} onCommit={() => { if (draft != null) onCommit(draft); setDraft(null) }} />
    </span>
  </ListRow>
}

function QueueRow({ track, index, onRemove, onPlayNext }: { track: PlayerTrack; index: number; onRemove(): void; onPlayNext(): void }) { const item = useSortable({ id: `${track.url ?? track.title}-${index}` }); return <div ref={item.setNodeRef} className={`list-row sortable${item.isDragging ? ' dragging' : ''}`} style={{ transform: CSS.Transform.toString(item.transform), transition: item.transition }}><button className="drag-handle" aria-label={`Reorder ${track.title}`} {...item.attributes} {...item.listeners}>⠿</button><span className="row-label">{track.title}<small>{track.uploader} · {formatDuration(track.duration_s)}</small></span><span className="row-actions"><PressableButton className="small soft" label={`Play ${track.title} next`} onClick={onPlayNext}>Play next</PressableButton><IconButton variant="danger" size={30} label={`Remove ${track.title}`} onClick={onRemove}>×</IconButton></span></div> }
export function GuildMusic() {
  const { guildId } = useParams(); const toast = useToast(); const player = usePlayer(guildId); const act = usePlayerAction(guildId); const [volume, setVolume] = useState<number | null>(null); const [query, setQuery] = useState(''); const [front, setFront] = useState(false); const [clearOpen, setClearOpen] = useState(false); const [queue, setQueue] = useState<PlayerTrack[]>([]); const elapsed = useElapsed(player.data, player.dataUpdatedAt); const sensors = useSensors(useSensor(PointerSensor), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }))
  useEffect(() => { setQueue(player.data?.queue ?? []) }, [player.data?.queue])
  // Failures are announced globally by the MutationCache in lib/query.ts, so
  // only the confirmation is passed here -- the call site is the only place
  // that knows the right words, and a generic "Saved" after every toggle is
  // noise that trains people to ignore the region.
  const run = (action: string, args?: Record<string, unknown>, confirm?: string) => {
    haptic(action === 'stop' ? 15 : 8)
    act.mutate({ action, args }, confirm ? { onSuccess: () => toast.success(confirm) } : undefined)
  }
  if (player.isPending) return <main className="app"><Skeleton lines={6} /></main>; if (player.error || !player.data) return <main className="app"><LargeTitleHeader title="Music" /><ErrorNote error={player.error} onRetry={() => player.refetch()} /><BackLink to={`/g/${guildId}`}>Back to the server</BackLink></main>
  const data = player.data; const paused = !!data.paused; const shownVolume = volume ?? data.volume ?? 50
  const reorder = (active: string | number, over: string | number | null) => { if (!over || active === over) return; const from = queue.findIndex((track, index) => `${track.url ?? track.title}-${index}` === active), to = queue.findIndex((track, index) => `${track.url ?? track.title}-${index}` === over); if (from < 0 || to < 0) return; const next = arrayMove(queue, from, to); setQueue(next); run('move', { from, to }) }
  // The undo lives in the toast host now. It used to be a hand-rolled
  // <div className="toast success"> rendered *after* the queue list, so on a
  // long queue it spent its entire life below the fold -- D3 was implemented
  // and unreachable.
  const remove = (index: number) => {
    const track = queue[index]
    setQueue(queue.filter((_, position) => position !== index))
    act.mutate({ action: 'remove', args: { index } }, {
      onSuccess: () => toast.success(`Removed ${track.title}`, {
        label: 'Undo',
        onClick: () => run('play', { query: track.url ?? track.title, mode: 'next' }),
      }),
    })
  }
  return <main className="app"><GuildShell guildId={guildId}><div className="player-live" aria-live="polite">{data.track ? `${data.track.title} ${paused ? 'paused' : 'playing'}` : 'Nothing playing'}</div><LargeTitleHeader title="Music" subtitle="Now playing, queue and playlists for this server." />
    <GlassSurface tier="thin" className="music-search"><label className="field"><span>Queue music</span><div className="search-field"><input className="search-input" aria-label="Song or URL to queue" value={query} onChange={event => setQuery(event.target.value)} placeholder="Song, artist, YouTube or Spotify URL" /><PressableButton disabled={!query.trim() || act.isPending} onClick={() => { run('play', { query, mode: front ? 'next' : 'queue' }, front ? 'Playing that next' : 'Queued'); setQuery('') }}>{front ? 'Play next' : 'Queue'}</PressableButton></div></label><Toggle label="Play next" checked={front} onChange={setFront} /><p className="muted small-note">Join a voice channel in Discord first. Zephyr will join your channel when it queues the track.</p></GlassSurface>
    {!data.live && <GlassSurface><p>Zephyr is not playing anything here.</p><p className="muted">Join a voice channel in Discord, then use the queue field above.</p></GlassSurface>}
    {data.track && <GlassSurface className="now-playing"><div className="now-playing-head">{data.track.thumbnail ? <img className="art" src={data.track.thumbnail} alt="" /> : <span className="art-placeholder" aria-hidden><DiscIcon /></span>}<div className="meta"><h2>{data.track.title}</h2><p>{data.track.uploader}{data.voice_channel_name ? ` · ${data.voice_channel_name}` : ''}</p></div></div><ProgressBar elapsed={elapsed} duration={data.duration_s ?? 0} onSeek={position => run('seek', { position })} /></GlassSurface>}
    {data.live && <><div className="transport"><IconButton variant="primary" size={52} label={paused ? 'Resume' : 'Pause'} onClick={() => run(paused ? 'resume' : 'pause')}>{paused ? <PlayIcon size={18} /> : <PauseIcon size={18} />}</IconButton><IconButton label="Skip" onClick={() => run('skip')}><SkipIcon /></IconButton><IconButton label="Shuffle" onClick={() => run('shuffle')}><ShuffleIcon /></IconButton><IconButton variant="danger" label="Stop" onClick={() => run('stop')}><StopIcon /></IconButton></div><ListGroup><ListRow label="Loop"><SegmentedControl values={LOOP_MODES} labels={LOOP_LABELS} value={data.loop ?? 'off'} onChange={mode => run('loop', { mode })} /></ListRow><ListRow label="Volume"><span className="row-actions"><span className="row-value mono">{shownVolume}%</span><Slider label="Volume" value={shownVolume} onChange={next => { setVolume(next); act.mutate({ action: 'volume', args: { volume: next } }, { onSettled: () => setVolume(null) }) }} /></span></ListRow><ListRow label="Autoplay" detail="Keep playing a YouTube Mix"><Toggle label="Autoplay" checked={!!data.autoplay} onChange={enabled => run('autoplay', { enabled })} /></ListRow></ListGroup><details className="effects"><summary>Audio effects</summary><ListGroup><EffectSlider label="Bass boost" value={data.effects?.bass_boost ?? 0} min={0} max={20} format={value => `+${value} dB`} onCommit={bass_boost => run('effects', { bass_boost })} /><EffectSlider label="Pitch" value={data.effects?.pitch ?? 1} min={0} max={2} step={.1} format={value => `${value.toFixed(1)}x`} onCommit={pitch => run('effects', { pitch })} />{TOGGLE_EFFECTS.map(({ key, label, detail }) => <ListRow key={key} label={label} detail={detail}><Toggle label={label} checked={!!data.effects?.[key]} onChange={value => run('effects', { [key]: value })} /></ListRow>)}</ListGroup><PressableButton variant="secondary" className="small" onClick={() => run('effects', { reset: true }, 'Effects reset')}>Reset effects</PressableButton></details></>}
    <div className="section-head"><h2>Queue{data.queue_length ? ` (${data.queue_length})` : ''}{data.queue_duration_s ? ` · ${formatDuration(data.queue_duration_s)}` : ''}</h2><PressableButton variant="danger" className="small" disabled={!queue.length} onClick={() => setClearOpen(true)}>Clear queue</PressableButton></div>{queue.length === 0 ? <GlassSurface tier="thin"><p className="muted">Nothing queued.</p></GlassSurface> : <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={event => reorder(event.active.id, event.over?.id ?? null)}><SortableContext items={queue.map((track, index) => `${track.url ?? track.title}-${index}`)} strategy={verticalListSortingStrategy}><ListGroup>{queue.map((track, index) => <QueueRow key={`${track.url ?? track.title}-${index}`} track={track} index={index} onPlayNext={() => run('play', { query: track.url ?? track.title, mode: 'next' }, `Playing ${track.title} next`)} onRemove={() => remove(index)} />)}</ListGroup></SortableContext></DndContext>}<PlaylistPanel guildId={guildId} /><ConfirmSheet open={clearOpen} onOpenChange={setClearOpen} title="Clear queue" description="Remove every queued track? The current track keeps playing." confirmLabel="Clear queue" onConfirm={() => { run('clear', undefined, 'Queue cleared'); setClearOpen(false) }} /><BackLink to={`/g/${guildId}`}>Back to the server</BackLink></GuildShell></main>
}
