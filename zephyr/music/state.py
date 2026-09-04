"""``VoiceState`` -- one guild's live playback.

Keyed on the guild rather than on a ``Context``, and driven through callbacks
rather than a reference to the cog. Both were already true before this split, and
both are what let the class move: it knows nothing about Redis and nothing about
the button view's permission model.
"""

import asyncio
import math
import time
from collections import deque

import discord
from async_timeout import timeout
from discord.ext import commands

from zephyr.core.logging import get_logger
from zephyr.music.common import (
    AUTOPLAY_ADD,
    AUTOPLAY_FETCH,
    AUTOPLAY_MEMORY,
    DEFAULT_SKIP_RATIO,
    SNAPSHOT_QUEUE_LIMIT,
    _video_id,
)
from zephyr.music.queue import SongQueue
from zephyr.music.sources import Track, YTDLSource
from zephyr.utils import embeds

log = get_logger(__name__)


class VoiceState:
    """Per-guild playback state.

    Takes a guild id and a channel id rather than a Context: a Context is only
    available from a slash command, and the state has to be reachable from callers
    that have neither (the Redis bridge, and any scheduled task).  ``_ctx`` was also
    captured from whichever command created the state and then used for every later
    error message, which was wrong once the state outlived that command.
    """

    def __init__(self, bot: commands.Bot, guild_id: int, *, channel_id: int | None = None):
        self.bot = bot
        self.guild_id = int(guild_id)
        # Sticky: the channel now-playing and errors go to, updated by each play
        # command.  Previously messages went to the channel of whoever enqueued the
        # *current* track, so a queue built from two channels made the now-playing
        # message hop between them mid-playback.
        self.np_channel_id = channel_id
        self.current: Track | None = None      # metadata; always safe to read
        self.source: YTDLSource | None = None  # live audio; only while playing
        self.voice: discord.VoiceClient = None
        self.next = asyncio.Event()
        self.songs = SongQueue()
        self.exists = True

        self._loop_mode = 'off'  # 'off', 'track', 'queue'
        self._volume = 0.5
        self.skip_votes = set()
        self.manual_stop = False

        self._16d_enabled = False
        self._reverb_enabled = False
        self._slowed_enabled = False
        self._slownrev_enabled = False
        self._nightcore_enabled = False
        self._vaporwave_enabled = False
        self._247_enabled = False
        # Per-guild, applied by MusicCog.apply_policy when the state is created.
        # Held on the state rather than read from the cog so _skip_threshold
        # stays a pure function of the state, which is what makes it testable
        # without a cog, a bot or a database.
        self.skip_ratio = DEFAULT_SKIP_RATIO
        self._autoplay_enabled = False
        self._pitch = 1.0
        self._bass_boost = None

        self._current_start_time = None
        self._current_position = 0.0
        self.audio_player = None
        # Bounded history of what autoplay has already served, so a Mix -- which
        # always leads with its seed video -- cannot put one song on repeat.
        self._recent_ids = deque(maxlen=AUTOPLAY_MEMORY)
        # Set by MusicCog.ensure_voice_state.  Callbacks rather than a back
        # reference to the cog, so this class still knows nothing about Redis
        # and nothing about the button view's permission model.
        self.on_change = None
        self.np_view_factory = None
        self.np_message: discord.Message | None = None

    def start_player(self):
        """Start the audio player task lazily; safe to call multiple times."""
        if self.audio_player is None or self.audio_player.done():
            self.audio_player = self.bot.loop.create_task(self.audio_player_task())

    def changed(self) -> None:
        """Announce a playback transition, without waiting for it to publish.

        Fire and forget on purpose: the audio player must not block on Redis, and
        a snapshot that fails to publish is corrected by the periodic loop three
        seconds later.  Never called from the after-callback, which runs on
        discord.py's player thread where creating a task is not safe.
        """
        if self.on_change is None:
            return
        try:
            self.bot.loop.create_task(self.on_change(self.guild_id))
        except RuntimeError:
            pass  # Loop already closed during shutdown.

    @property
    def loop(self):
        return self._loop_mode

    @loop.setter
    def loop(self, value: str):
        self._loop_mode = value

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = value
        if self.source is not None:
            self.source.volume = value

    @property
    def is_playing(self):
        return self.voice and self.current

    @property
    def elapsed(self):
        if self._current_start_time is None:
            return self._current_position
        return self._current_position + (time.time() - self._current_start_time)

    def get_ffmpeg_options(self, seek_position: float = None):
        options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }
        if seek_position is not None and seek_position > 0:
            options['before_options'] += f' -ss {seek_position}'
        filters = []
        if self._16d_enabled:
            filters.append("apulsator=hz=0.08")
        if self._nightcore_enabled:
            filters.append("atempo=1.3,asetrate=48000")
        elif self._vaporwave_enabled:
            filters.append("atempo=0.7,asetrate=32000")
        if self._reverb_enabled:
            filters.append("reverb=reverbdry=50:reverbwet=50")
        if self._slowed_enabled:
            filters.append("atempo=0.98,asetrate=44100,aresample=44100")
        if self._slownrev_enabled:
            filters.append("atempo=0.98,asetrate=44100,aresample=44100,reverb=reverbdry=50:reverbwet=50")
        if self._pitch != 1.0:
            filters.append(f"rubberband=pitch={self._pitch}")
        if self._bass_boost is not None:
            filters.append(f"bass=g={self._bass_boost}")
        if filters:
            options['options'] += f' -filter:a "{",".join(filters)}"'
        return options

    def effects(self) -> dict:
        """The effect chain as plain data, for the snapshot and the web UI."""
        return {
            'bass_boost': self._bass_boost,
            'pitch': self._pitch,
            'nightcore': self._nightcore_enabled,
            'vaporwave': self._vaporwave_enabled,
            'reverb': self._reverb_enabled,
            'slowed': self._slowed_enabled,
            'slownrev': self._slownrev_enabled,
            'sixteen_d': self._16d_enabled,
        }

    def snapshot(self) -> dict:
        """Everything the dashboard needs to render the player.

        The queue is truncated: a 200-track queue re-serialised into Redis every
        few seconds is a lot of bytes for a list nobody scrolls to the end of.
        ``queue_length`` and ``queue_duration_s`` are computed over the whole
        queue regardless, so the UI can say "and 150 more" honestly.
        """
        upcoming = list(self.songs)
        channel = self.voice.channel if self.voice and self.voice.is_connected() else None
        return {
            'guild_id': str(self.guild_id),
            'connected': channel is not None,
            'voice_channel_id': str(channel.id) if channel else None,
            'voice_channel_name': channel.name if channel else None,
            'text_channel_id': str(self.np_channel_id) if self.np_channel_id else None,
            'playing': bool(self.voice and self.voice.is_playing()),
            'paused': bool(self.voice and self.voice.is_paused()),
            'position_s': round(self.elapsed, 1) if self.current else 0.0,
            'duration_s': self.current.duration_seconds if self.current else 0,
            'loop': self.loop,
            'volume': int(round(self._volume * 100)),
            'autoplay': self._autoplay_enabled,
            'always_on': self._247_enabled,
            'effects': self.effects(),
            'track': self.current.to_payload() if self.current else None,
            'queue': [track.to_payload() for track in upcoming[:SNAPSHOT_QUEUE_LIMIT]],
            'queue_length': len(upcoming),
            'queue_duration_s': sum(track.duration_seconds or 0 for track in upcoming),
        }

    def channel(self) -> discord.abc.Messageable | None:
        """The channel now-playing and errors are posted to, if it is still reachable."""
        if self.np_channel_id is None:
            return None
        return self.bot.get_channel(self.np_channel_id)

    async def _notify(self, **kwargs) -> discord.Message | None:
        """Best-effort message to the sticky channel. Never raises."""
        channel = self.channel()
        if channel is None:
            return None
        try:
            return await channel.send(**kwargs)
        except Exception as exc:
            log.exception("Could not post to channel %s", self.np_channel_id)
            return None

    async def announce_now_playing(self, track: 'Track') -> None:
        """Replace the now-playing message with one for ``track``.

        The previous message is deleted rather than left behind: it is a live
        control surface, and a channel accumulating one stale set of buttons per
        track is both noise and a trap.  A bot may always delete its own
        messages, so this needs no extra permission.
        """
        await self.retire_now_playing(delete=True)
        view = self.np_view_factory() if self.np_view_factory else None
        self.np_message = await self._notify(embed=track.create_embed(elapsed=0.0), view=view)

    async def refresh_now_playing(self) -> None:
        """Redraw the progress bar.  Called on a slow loop, never per second."""
        if self.np_message is None or self.current is None:
            return
        try:
            await self.np_message.edit(embed=self.current.create_embed(elapsed=self.elapsed))
        except discord.NotFound:
            # Somebody deleted it; stop trying to edit a message that is gone.
            self.np_message = None
        except Exception as exc:
            log.exception("Could not refresh the now-playing message")

    async def retire_now_playing(self, *, delete: bool = False) -> None:
        """Delete the now-playing message, or leave it with dead buttons disabled.

        Disabling matters on the way out: a view with ``timeout=None`` never
        expires by itself, so without this the last message of a session keeps
        buttons that look live and silently do nothing.
        """
        message, self.np_message = self.np_message, None
        if message is None:
            return
        try:
            if delete:
                await message.delete()
            else:
                await message.edit(view=None)
        except discord.NotFound:
            pass
        except Exception as exc:
            log.exception("Could not retire the now-playing message")

    async def _extend_with_radio(self) -> int:
        """Top the queue up from YouTube's Mix for the last track played.

        A Mix (``list=RD<video id>``) is YouTube's own related-tracks radio, so
        this needs no recommendation logic of its own -- one flat extraction
        yields dozens of entries.  Anything played recently is filtered out,
        because a Mix always leads with its seed video and autoplay would
        otherwise put the same song on repeat.
        """
        seed = _video_id(self.current.url) if self.current else None
        if not seed:
            return 0
        try:
            tracks = await YTDLSource.resolve_tracks(
                f"https://www.youtube.com/watch?v={seed}&list=RD{seed}",
                requester_id=self.bot.user.id if self.bot.user else 0,
                requester_mention='Autoplay',
                loop=self.bot.loop,
                max_entries=AUTOPLAY_FETCH,
            )
        except Exception as exc:
            log.exception("Could not build a radio for %s", seed)
            return 0

        added = 0
        for track in tracks:
            key = _video_id(track.url) or track.url
            if not key or key in self._recent_ids:
                continue
            track.source = 'autoplay'
            self.songs.put_nowait(track)
            added += 1
            if added >= AUTOPLAY_ADD:
                break
        if added:
            log.info("Queued %d track(s) from the radio for %s", added, seed)
        return added

    def _remember(self, track: 'Track') -> None:
        key = _video_id(track.url) or track.url
        if key:
            self._recent_ids.append(key)

    async def audio_player_task(self):
        while True:
            self.next.clear()
            self.skip_votes.clear()

            # Refill before the idle timer starts, not after it fires: waiting
            # would disconnect the bot three minutes into an autoplay session.
            if self._autoplay_enabled and not len(self.songs) and self.loop == 'off':
                await self._extend_with_radio()

            try:
                if self.loop == 'track' and self.current:
                    track = self.current            # replay the same track
                else:
                    async with timeout(180 if not self._247_enabled else None):
                        track = await self.songs.get()
                    self.current = track
                    if self.loop == 'queue' and track:
                        # Re-queue it so it plays again after everything else.
                        self.songs.put_nowait(track)
                source = await YTDLSource.from_track(track, ffmpeg_options=self.get_ffmpeg_options(),
                                                     volume=self._volume, loop=self.bot.loop)
            except asyncio.TimeoutError:
                # Idle timeout. cancel_player=False because we are *inside* the player
                # task: cancelling and awaiting ourselves only works by accident.
                await self.stop(cancel_player=False)
                self.exists = False
                return
            except Exception as e:
                await self._notify(embed=embeds.error(f'❌ Failed to load next track: {e}'))
                log.exception("Failed to load the next track")
                self.current = None
                continue

            try:
                self.source = source
                self._current_start_time = time.time()
                self._current_position = 0.0
                self.voice.play(source, after=self.play_next_song)
                self._remember(track)
                self.changed()
                await self.announce_now_playing(track)
            except Exception as e:
                # The source owns a live ffmpeg process; if play() never took ownership
                # of it, nothing else will ever clean it up.
                if self.source is source:
                    self.source = None
                try:
                    source.cleanup()
                except Exception:
                    pass
                await self._notify(content=f'An error occurred while playing: {e}')
                log.exception("Playback failed")
                self.play_next_song()

            await self.next.wait()

    def play_next_song(self, error=None):
        """Advance the queue once the current track finishes.

        discord.py invokes this from its AudioPlayer *thread* (discord/player.py's
        ``AudioPlayer.run`` calls ``_call_after`` in a ``finally``), not from the event
        loop -- and ``asyncio.Event.set`` is not thread-safe, so it has to be
        marshalled back onto the loop.  Calling it directly happened to work most of
        the time, which is the worst kind of bug.

        ``manual_stop`` is read cross-thread too; it is safe only because
        ``restart_current`` sets it *before* calling ``voice.stop()``.
        """
        if error:
            log.error("FFmpeg failed during playback: %s", error)
        if self.manual_stop:
            self.manual_stop = False
            return
        try:
            self.bot.loop.call_soon_threadsafe(self.next.set)
        except RuntimeError:
            # The loop is already closed (shutdown); nothing left to advance.
            pass

    def skip(self):
        self.skip_votes.clear()
        if self.is_playing:
            self.voice.stop()

    async def stop(self, *, cancel_player: bool = True):
        """Clear the queue and disconnect.

        ``cancel_player=False`` is for callers running *inside* audio_player_task --
        the idle-timeout path did this and ended up cancelling and awaiting its own
        task, which only worked because the CancelledError happened to be delivered
        at that await and swallowed below.
        """
        self.songs.clear()
        self._loop_mode = 'off'
        self.manual_stop = False
        self.current = None
        self.source = None
        if cancel_player and self.audio_player and not self.audio_player.done():
            self.audio_player.cancel()
            try:
                await self.audio_player
            except asyncio.CancelledError:
                pass
        if self.voice:
            await self.voice.disconnect()
            self.voice = None
        await self.retire_now_playing()
        # `exists` is the flag peek_voice_state reads to answer "is anything
        # playing". Only the idle-timeout path used to set it, so every other
        # route through stop() -- /stop, a bridge stop, a disconnect -- left a
        # torn-down state advertising itself as live, and ensure_voice_state
        # then handed that dead object to the next /play.
        self.exists = False
        self.changed()

    def _skip_threshold(self):
        """How many votes end the current track.

        Clamped to the number of people who could possibly vote: a ratio of 1.0
        in a channel of three must need three, not four, or the vote can never
        pass and /skip becomes a command that does nothing.
        """
        if not self.voice or not self.voice.channel:
            return 1
        non_bot = [m for m in self.voice.channel.members if not m.bot]
        if not non_bot:
            return 1
        ratio = self.skip_ratio or DEFAULT_SKIP_RATIO
        return max(1, min(len(non_bot), math.ceil(len(non_bot) * ratio)))

    async def restart_current(self, interaction: discord.Interaction = None, preserve_position: bool = True):
        """Recreate and restart the current track (used by effects and seek)."""
        if not self.is_playing:
            return
        elapsed = self.elapsed if preserve_position else 0.0
        track = self.current
        # manual_stop must be set *before* voice.stop(), or the after-callback fires
        # first and advances the queue instead of letting us replace the source.
        self.manual_stop = True
        self.voice.stop()
        try:
            # The old source is reaped by discord.py: AudioPlayer.run cleans it up in a
            # finally block once voice.stop() ends the player thread.
            source = await YTDLSource.from_track(track,
                                                 ffmpeg_options=self.get_ffmpeg_options(seek_position=elapsed),
                                                 volume=self._volume, loop=self.bot.loop)
            self.source = source
            self._current_position = elapsed
            self._current_start_time = time.time()
            self.voice.play(source, after=self.play_next_song)
            self.manual_stop = False
        except Exception as e:
            self.manual_stop = False
            raise e
