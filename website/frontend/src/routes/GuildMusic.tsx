import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError } from '../lib/api'
import { haptic } from '../lib/haptics'
import { formatDuration, useElapsed, usePlayer, usePlayerAction } from '../lib/player'
import { ErrorNote } from '../components/ErrorNote'
import { GuildShell } from '../components/GuildNav'
import { PlaylistPanel } from '../components/PlaylistPanel'
import { BackLink, GlassSurface, IconButton, LargeTitleHeader, ListGroup, ListRow, PressableButton, SegmentedControl, Skeleton, Slider, Toggle } from '../components/ios'
import { PauseIcon, PlayIcon, ShuffleIcon, SkipIcon, StopIcon } from '../components/icons'
import type { Player } from '../types/api'

const LOOP_MODES = ['off', 'track', 'queue'] as const
const LOOP_LABELS = { off: 'Off', track: 'Track', queue: 'Queue' }

// A 409 is the bot refusing -- not in the voice channel, no DJ role, nothing
// playing. It is the user's answer, not an outage, so it is shown inline and the
// generic "check your connection" wording is kept for genuine network failures.
function refusalMessage(error: unknown) {
  return error instanceof ApiError && error.status === 409 ? error.message : null
}

function ProgressBar({ elapsed, duration }: { elapsed: number; duration: number }) {
  const ratio = duration > 0 ? Math.min(1, elapsed / duration) : 0
  return <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={duration} aria-valuenow={Math.floor(elapsed)}>
    <i style={{ width: `${ratio * 100}%` }} />
  </div>
}

function NowPlaying({ player, elapsed }: { player: Player; elapsed: number }) {
  const track = player.track!
  return <GlassSurface className="now-playing">
    <div className="now-playing-head">
      {track.thumbnail
        ? <img className="art" src={track.thumbnail} alt="" />
        : <span className="art-placeholder" aria-hidden>track art</span>}
      <div className="meta">
        <h2>{track.title}</h2>
        <p>{track.uploader}{player.voice_channel_name ? ` · ${player.voice_channel_name}` : ''}</p>
      </div>
    </div>
    <ProgressBar elapsed={elapsed} duration={player.duration_s ?? 0} />
    <p className="times"><span>{formatDuration(elapsed)}</span><span>{formatDuration(player.duration_s)}</span></p>
  </GlassSurface>
}

export function GuildMusic() {
  const { guildId } = useParams()
  const player = usePlayer(guildId)
  const act = usePlayerAction(guildId)
  const [volume, setVolume] = useState<number | null>(null)
  const elapsed = useElapsed(player.data, player.dataUpdatedAt)

  const run = (action: string, args?: Record<string, unknown>) => {
    haptic(action === 'stop' ? 15 : 8)
    act.mutate({ action, args })
  }

  if (player.isPending) return <main className="app"><Skeleton lines={6} /></main>
  if (player.error || !player.data) {
    return <main className="app">
      <LargeTitleHeader title="Music" />
      <ErrorNote error={player.error} onRetry={() => player.refetch()} />
      <BackLink to={`/g/${guildId}`}>Back to the server</BackLink>
    </main>
  }

  const data = player.data
  const paused = !!data.paused
  const refusal = refusalMessage(act.error)
  // A slider being dragged must not be yanked back by the next poll, so the
  // local value wins until the mutation that carries it has settled.
  const shownVolume = volume ?? data.volume ?? 50

  return <main className="app"><GuildShell guildId={guildId}>
    <LargeTitleHeader title="Music" subtitle="Now playing, queue and playlists for this server." />

    {!data.live && <GlassSurface>
      <p>Zephyr is not playing anything here.</p>
      <p className="muted">Either it is offline, or it has not joined a voice channel in this server. Start something with <code>/play</code> in Discord, or join a voice channel and queue a playlist below.</p>
    </GlassSurface>}

    {data.track && <NowPlaying player={data} elapsed={elapsed} />}

    {data.live && <>
      <div className="transport">
        <IconButton variant="primary" size={52} label={paused ? 'Resume' : 'Pause'} onClick={() => run(paused ? 'resume' : 'pause')}>
          {paused ? <PlayIcon size={18} /> : <PauseIcon size={18} />}
        </IconButton>
        <IconButton label="Skip" onClick={() => run('skip')}><SkipIcon /></IconButton>
        <IconButton label="Shuffle" onClick={() => run('shuffle')}><ShuffleIcon /></IconButton>
        <IconButton variant="danger" label="Stop" onClick={() => run('stop')}><StopIcon /></IconButton>
      </div>

      <ListGroup>
        <ListRow label="Loop">
          <span className="row-actions">
            <SegmentedControl values={[...LOOP_MODES]} labels={LOOP_LABELS} value={data.loop ?? 'off'} onChange={mode => run('loop', { mode })} />
          </span>
        </ListRow>
        <ListRow label="Volume">
          <span className="row-actions">
            <span className="row-value mono">{shownVolume}%</span>
            <Slider
              label="Volume"
              value={shownVolume}
              onChange={next => { setVolume(next); act.mutate({ action: 'volume', args: { volume: next } }, { onSettled: () => setVolume(null) }) }}
            />
          </span>
        </ListRow>
        <ListRow label="Autoplay" detail="Keep playing a YouTube Mix when the queue runs out">
          <span className="row-actions"><Toggle label="Autoplay" checked={!!data.autoplay} onChange={enabled => run('autoplay', { enabled })} /></span>
        </ListRow>
      </ListGroup>
    </>}

    {refusal && <ErrorNote error={act.error} />}
    {act.error && !refusal && <ErrorNote error={act.error} onRetry={() => act.reset()} />}

    <h2>Queue{data.queue_length ? ` (${data.queue_length})` : ''}</h2>
    {data.queue.length === 0
      ? <GlassSurface tier="thin"><p className="muted">Nothing queued.</p></GlassSurface>
      : <ListGroup>
        {data.queue.map((track, index) => <ListRow
          key={`${track.url ?? track.title}-${index}`}
          label={track.title}
          detail={`${track.uploader} · ${formatDuration(track.duration_s)}`}
        >
          <span className="row-actions">
            <PressableButton className="small soft" onClick={() => run('jump', { index })}>Play</PressableButton>
            <IconButton variant="danger" size={30} label={`Remove ${track.title}`} onClick={() => run('remove', { index })}>×</IconButton>
          </span>
        </ListRow>)}
      </ListGroup>}
    {/* The snapshot carries a bounded slice of the queue. Saying so is better
        than silently implying the list ends where the payload does. */}
    {(data.queue_length ?? 0) > data.queue.length && <p className="muted">and {(data.queue_length ?? 0) - data.queue.length} more…</p>}

    <PlaylistPanel guildId={guildId} />

    <BackLink to={`/g/${guildId}`}>Back to the server</BackLink>
  </GuildShell></main>
}
