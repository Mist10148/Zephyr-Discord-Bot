import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '../lib/api'
import { haptic } from '../lib/haptics'
import { formatDuration, useElapsed, usePlayer, usePlayerAction } from '../lib/player'
import { ErrorNote } from '../components/ErrorNote'
import { PlaylistPanel } from '../components/PlaylistPanel'
import { GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, SegmentedControl, Skeleton, Slider, Toggle } from '../components/ios'
import type { Player } from '../types/api'

const LOOP_MODES = ['off', 'track', 'queue'] as const

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
    {track.thumbnail && <img className="art" src={track.thumbnail} alt="" />}
    <div className="stack">
      <h2>{track.title}</h2>
      <p className="muted">{track.uploader}{player.voice_channel_name ? ` • ${player.voice_channel_name}` : ''}</p>
      <ProgressBar elapsed={elapsed} duration={player.duration_s ?? 0} />
      <p className="muted times"><span>{formatDuration(elapsed)}</span><span>{formatDuration(player.duration_s)}</span></p>
    </div>
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
      <p><Link to={`/g/${guildId}`}>Back to the server</Link></p>
    </main>
  }

  const data = player.data
  const paused = !!data.paused
  const refusal = refusalMessage(act.error)
  // A slider being dragged must not be yanked back by the next poll, so the
  // local value wins until the mutation that carries it has settled.
  const shownVolume = volume ?? data.volume ?? 50

  return <main className="app">
    <LargeTitleHeader title="Music" />

    {!data.live && <GlassSurface>
      <p>Zephyr is not playing anything here.</p>
      <p className="muted">Either it is offline, or it has not joined a voice channel in this server. Start something with <code>/play</code> in Discord, or join a voice channel and queue a playlist below.</p>
    </GlassSurface>}

    {data.track && <NowPlaying player={data} elapsed={elapsed} />}

    {data.live && <>
      <div className="transport">
        <PressableButton variant="secondary" onClick={() => run(paused ? 'resume' : 'pause')}>{paused ? '▶︎ Resume' : '❙❙ Pause'}</PressableButton>
        <PressableButton variant="secondary" onClick={() => run('skip')}>⏭ Skip</PressableButton>
        <PressableButton variant="secondary" onClick={() => run('shuffle')}>🔀 Shuffle</PressableButton>
        <PressableButton variant="danger" onClick={() => run('stop')}>⏹ Stop</PressableButton>
      </div>

      <ListGroup>
        <ListRow label="Loop">
          <SegmentedControl values={[...LOOP_MODES]} value={data.loop ?? 'off'} onChange={mode => run('loop', { mode })} />
        </ListRow>
        <ListRow label="Volume" detail={`${shownVolume}%`}>
          <Slider
            label="Volume"
            value={shownVolume}
            onChange={next => { setVolume(next); act.mutate({ action: 'volume', args: { volume: next } }, { onSettled: () => setVolume(null) }) }}
          />
        </ListRow>
        <ListRow label="Autoplay" detail="Keep playing a YouTube Mix when the queue runs out">
          <Toggle checked={!!data.autoplay} onChange={enabled => run('autoplay', { enabled })} />
        </ListRow>
      </ListGroup>
    </>}

    {refusal && <ErrorNote error={act.error} />}
    {act.error && !refusal && <ErrorNote error={act.error} onRetry={() => act.reset()} />}

    <h2>Queue{data.queue_length ? ` (${data.queue_length})` : ''}</h2>
    {data.queue.length === 0
      ? <GlassSurface><p className="muted">Nothing queued.</p></GlassSurface>
      : <ListGroup>
        {data.queue.map((track, index) => <ListRow
          key={`${track.url ?? track.title}-${index}`}
          label={track.title}
          detail={`${track.uploader} • ${formatDuration(track.duration_s)}`}
        >
          <span className="row-actions">
            <PressableButton variant="secondary" onClick={() => run('jump', { index })}>Play</PressableButton>
            <PressableButton variant="secondary" onClick={() => run('remove', { index })}>Remove</PressableButton>
          </span>
        </ListRow>)}
      </ListGroup>}
    {/* The snapshot carries a bounded slice of the queue. Saying so is better
        than silently implying the list ends where the payload does. */}
    {(data.queue_length ?? 0) > data.queue.length && <p className="muted">and {(data.queue_length ?? 0) - data.queue.length} more…</p>}

    <PlaylistPanel guildId={guildId} />

    <p><Link to={`/g/${guildId}`}>Back to the server</Link></p>
  </main>
}
