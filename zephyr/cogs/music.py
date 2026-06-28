"""Music system (Groovy-inspired): YouTube/Spotify playback, queue, audio effects,
seek controls, and lyrics.

Ported 1:1 from the original bot.py (lines 899-2211). Time helpers come from
``utils.time_utils`` and the FFmpeg path from ``core.ffmpeg``.
"""

import math
import random
import asyncio
import functools
import itertools
import traceback
import time
import re
from collections import deque

import aiohttp
import requests
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select
from async_timeout import timeout
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from zephyr.config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
from zephyr.core.ffmpeg import FFMPEG_PATH
from zephyr.utils.time_utils import _parse_user_time, _format_timestamp


# ---------------------------------------------------------------------------
# URL / query helpers
# ---------------------------------------------------------------------------
def _sanitize_search(search: str) -> str:
    """Strip Discord markdown brackets and whitespace from a user query."""
    return search.strip().strip("<>").strip()


_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def _is_url(search: str) -> bool:
    return bool(_URL_RE.match(_sanitize_search(search)))


def _is_spotify_url(search: str) -> bool:
    s = _sanitize_search(search).lower()
    if s.startswith("spotify:"):
        return True
    return ("spotify.com" in s or "spotify.link" in s) and _is_url(s)


def _is_youtube_url(search: str) -> bool:
    s = _sanitize_search(search).lower()
    return _is_url(search) and ("youtube.com" in s or "youtu.be" in s or "youtube" in s)


def _is_youtube_playlist(search: str) -> bool:
    s = _sanitize_search(search).lower()
    return _is_youtube_url(search) and ("list=" in s or "/playlist" in s)


def _is_audio_file_url(search: str) -> bool:
    s = _sanitize_search(search).lower()
    return _is_url(search) and s.endswith((".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma"))


class VoiceError(Exception):
    pass


class YTDLError(Exception):
    pass


class YTDLSource(discord.PCMVolumeTransformer):
    YTDL_OPTIONS = {
        'format': 'bestaudio/best',
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': False,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
    }

    DEFAULT_FFMPEG_OPTIONS = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn',
    }

    ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, ctx: commands.Context, source: discord.FFmpegPCMAudio, *, data: dict, volume: float = 0.5):
        super().__init__(source, volume)
        self.requester = ctx.author
        self.channel = ctx.channel
        self.data = data
        self.uploader = data.get('uploader', 'Unknown')
        self.uploader_url = data.get('uploader_url')
        date = data.get('upload_date')
        self.upload_date = f"{date[6:8]}.{date[4:6]}.{date[0:4]}" if date else "Unknown"
        self.title = data.get('title', 'Unknown')
        self.thumbnail = data.get('thumbnail')
        self.description = data.get('description', '')
        self.duration = self.parse_duration(int(data.get('duration'))) if data.get('duration') else "Unknown"
        self.duration_seconds = int(data.get('duration')) if data.get('duration') else 0
        self.url = data.get('webpage_url', data.get('url'))
        self.views = data.get('view_count')
        self.likes = data.get('like_count')
        self.stream_url = data.get('url')

    def __str__(self):
        return f'**{self.title}** by **{self.uploader}**'

    @classmethod
    async def create_source(cls, ctx: commands.Context, search: str, *, loop: asyncio.AbstractEventLoop = None,
                            ffmpeg_options: dict = None, max_entries: int = 200):
        loop = loop or asyncio.get_event_loop()
        search = _sanitize_search(search)
        as_url = _is_url(search)
        # Explicitly route plain-text queries through YouTube search. Recent
        # yt-dlp/YouTube changes make ``default_search: 'auto'`` unreliable
        # and can return a generic extractor with no entries.
        query = search if as_url else f"ytsearch10:{search}"

        def _entry_url(entry: dict) -> str | None:
            """Best-effort URL from a flat yt-dlp entry."""
            if not entry:
                return None
            url = entry.get('url') or entry.get('webpage_url')
            if not url and entry.get('id'):
                # Use the extractor's canonical URL when possible.
                extractor = entry.get('extractor_key', 'youtube')
                if extractor.startswith('Youtube'):
                    url = f"https://www.youtube.com/watch?v={entry['id']}"
                else:
                    url = f"https://www.youtube.com/watch?v={entry['id']}"
            return url

        async def _extract_one(url: str):
            """Extract a single stream URL with a bounded timeout."""
            partial = functools.partial(cls.ytdl.extract_info, url, download=False)
            return await asyncio.wait_for(loop.run_in_executor(None, partial), timeout=15)

        try:
            partial = functools.partial(cls.ytdl.extract_info, query, download=False, process=False)
            data = await loop.run_in_executor(None, partial)
            if data is None:
                raise YTDLError(f'Could not find anything that matches `{search}`')

            # Plain text search -> only return the top result instead of
            # enqueueing every search result.
            if not as_url:
                entries = list(data.get('entries', []))
                if not entries:
                    raise YTDLError(f'Could not find anything that matches `{search}`')
                top_url = _entry_url(entries[0])
                if not top_url:
                    raise YTDLError(f'Could not find anything that matches `{search}`')
                info = await _extract_one(top_url)
                if info is None or 'url' not in info:
                    raise YTDLError(f'Could not fetch `{top_url}`')
                return cls(ctx,
                           discord.FFmpegPCMAudio(info['url'], executable=FFMPEG_PATH,
                                                  **(ffmpeg_options or cls.DEFAULT_FFMPEG_OPTIONS)),
                           data=info)

            # Single URL that resolves directly (video, audio file, etc.)
            if 'entries' not in data:
                webpage_url = data.get('webpage_url', data.get('url'))
                if not webpage_url:
                    raise YTDLError(f'Could not fetch `{search}`')
                processed_info = await _extract_one(webpage_url)
                if processed_info is None:
                    raise YTDLError(f'Could not fetch `{webpage_url}`')
                if 'entries' in processed_info:
                    # URL looked like a single item but yt-dlp returned a playlist anyway.
                    entries = list(processed_info['entries'])[:max_entries] if max_entries else list(processed_info['entries'])
                    partial = functools.partial(cls._build_sources_from_entries, ctx, entries, ffmpeg_options)
                    return await loop.run_in_executor(None, partial)
                return cls(ctx,
                           discord.FFmpegPCMAudio(processed_info['url'], executable=FFMPEG_PATH,
                                                  **(ffmpeg_options or cls.DEFAULT_FFMPEG_OPTIONS)),
                           data=processed_info)

            # Playlist / search result with multiple entries
            entries = list(data['entries'])[:max_entries] if max_entries else list(data['entries'])
            partial = functools.partial(cls._build_sources_from_entries, ctx, entries, ffmpeg_options)
            return await loop.run_in_executor(None, partial)

        except asyncio.TimeoutError:
            raise YTDLError(f'Request timed out while processing `{search}`. Try a shorter query or playlist.')
        except YTDLError:
            raise
        except Exception as e:
            traceback.print_exc()
            raise YTDLError(f"Failed to process `{search}`: {e}")

    @classmethod
    def _build_sources_from_entries(cls, ctx: commands.Context, entries: list, ffmpeg_options: dict = None):
        """Build YTDLSource objects from a list of flat entries, skipping failures."""
        sources = []
        skipped = 0

        def _entry_url(entry: dict) -> str | None:
            if not entry:
                return None
            url = entry.get('url') or entry.get('webpage_url')
            if not url and entry.get('id'):
                url = f"https://www.youtube.com/watch?v={entry['id']}"
            return url

        for entry in entries:
            entry_url = _entry_url(entry)
            if not entry_url:
                skipped += 1
                continue
            try:
                processed_info = cls.ytdl.extract_info(entry_url, download=False)
            except Exception as e:
                print(f"[Playlist Skip] {entry_url}: {e}")
                skipped += 1
                continue
            if processed_info and 'url' in processed_info:
                sources.append(cls(ctx,
                                   discord.FFmpegPCMAudio(processed_info['url'], executable=FFMPEG_PATH,
                                                          **(ffmpeg_options or cls.DEFAULT_FFMPEG_OPTIONS)),
                                   data=processed_info))
            else:
                skipped += 1

        if not sources:
            raise YTDLError('Could not resolve any tracks from the playlist. All entries were unavailable or unsupported.')
        if skipped:
            print(f"[Playlist] Added {len(sources)} tracks, skipped {skipped}.")
        return sources

    @classmethod
    async def search_sources(cls, ctx: commands.Context, search: str, *, loop: asyncio.AbstractEventLoop = None,
                             max_results: int = 10):
        """Return a list of YTDLSource entries for a search query (no FFmpeg source yet)."""
        loop = loop or asyncio.get_event_loop()
        search = _sanitize_search(search)
        query = f"ytsearch{max_results}:{search}"

        def _entry_url(entry: dict) -> str | None:
            if not entry:
                return None
            url = entry.get('url') or entry.get('webpage_url')
            if not url and entry.get('id'):
                url = f"https://www.youtube.com/watch?v={entry['id']}"
            return url

        try:
            partial = functools.partial(cls.ytdl.extract_info, query, download=False, process=False)
            data = await loop.run_in_executor(None, partial)
            if data is None or 'entries' not in data:
                return []
            entries = []
            for entry in data['entries']:
                url = _entry_url(entry)
                if not url:
                    continue
                partial = functools.partial(cls.ytdl.extract_info, url, download=False)
                processed = await loop.run_in_executor(None, partial)
                if processed and 'url' in processed:
                    entries.append(cls(ctx,
                                       discord.FFmpegPCMAudio(processed['url'], executable=FFMPEG_PATH,
                                                              **cls.DEFAULT_FFMPEG_OPTIONS),
                                       data=processed))
            return entries
        except Exception as e:
            traceback.print_exc()
            raise YTDLError(f"Failed to search `{search}`: {e}")

    @staticmethod
    def parse_duration(duration: int):
        if not duration:
            return 'Unknown'
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        parts = []
        if days > 0:
            parts.append(f'{days} days')
        if hours > 0:
            parts.append(f'{hours} hours')
        if minutes > 0:
            parts.append(f'{minutes} minutes')
        if seconds > 0:
            parts.append(f'{seconds} seconds')
        return ', '.join(parts) if parts else '0 seconds'


class Song:
    __slots__ = ('source', 'requester')

    def __init__(self, source: YTDLSource):
        self.source = source
        self.requester = source.requester

    def create_embed(self, elapsed: float = None):
        embed = discord.Embed(title='🎵 Now playing',
                              description=f'```css\n{self.source.title}\n```',
                              color=discord.Color.blurple())
        if elapsed is not None and self.source.duration_seconds > 0:
            elapsed = max(0.0, min(elapsed, self.source.duration_seconds))
            bar = self._progress_bar(elapsed, self.source.duration_seconds)
            embed.add_field(name='Progress',
                            value=f"`{_format_timestamp(elapsed)} / {_format_timestamp(self.source.duration_seconds)}`\n{bar}",
                            inline=False)
        embed.add_field(name='Duration', value=self.source.duration, inline=True)
        embed.add_field(name='Requested by', value=self.requester.mention, inline=True)
        embed.add_field(name='Uploader', value=f'[{self.source.uploader}]({self.source.uploader_url})', inline=True)
        embed.add_field(name='URL', value=f'[Click]({self.source.url})', inline=True)
        embed.set_thumbnail(url=self.source.thumbnail)
        return embed

    @staticmethod
    def _progress_bar(elapsed: float, total: int, length: int = 15) -> str:
        ratio = elapsed / total if total else 0
        ratio = max(0.0, min(1.0, ratio))
        filled = int(ratio * (length - 1))
        bar = '▬' * filled + '🔘' + '▬' * (length - filled - 1)
        return bar


class SongQueue:
    """Async-friendly queue that supports inserting items at the front."""

    def __init__(self):
        self._queue = deque()
        self._event = asyncio.Event()

    async def get(self):
        while not self._queue:
            self._event.clear()
            await self._event.wait()
        item = self._queue.popleft()
        return item

    def put_nowait(self, item):
        self._queue.append(item)
        self._event.set()

    def add_to_front(self, item):
        self._queue.appendleft(item)
        self._event.set()

    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(itertools.islice(self._queue, item.start, item.stop, item.step))
        return self._queue[item]

    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return len(self._queue)

    def clear(self):
        self._queue.clear()

    def shuffle(self):
        random.shuffle(self._queue)

    def remove(self, index: int):
        del self._queue[index]

    def move(self, from_index: int, to_index: int):
        if from_index < 0 or from_index >= len(self._queue):
            raise IndexError("Invalid source index")
        if to_index < 0 or to_index >= len(self._queue):
            raise IndexError("Invalid destination index")
        item = self._queue[from_index]
        del self._queue[from_index]
        self._queue.insert(to_index, item)


class VoiceState:
    def __init__(self, bot: commands.Bot, ctx: commands.Context):
        self.bot = bot
        self._ctx = ctx
        self.current: Song = None
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
        self._pitch = 1.0
        self._bass_boost = None

        self._current_start_time = None
        self._current_position = 0.0
        self.audio_player = None

    def start_player(self):
        """Start the audio player task lazily; safe to call multiple times."""
        if self.audio_player is None or self.audio_player.done():
            self.audio_player = self.bot.loop.create_task(self.audio_player_task())

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
        if self.is_playing and self.current:
            self.current.source.volume = value

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

    async def audio_player_task(self):
        while True:
            self.next.clear()
            self.skip_votes.clear()

            try:
                if self.loop == 'track' and self.current:
                    # Replay the current song
                    source = await YTDLSource.create_source(self._ctx, self.current.source.url,
                                                            loop=self.bot.loop, ffmpeg_options=self.get_ffmpeg_options())
                else:
                    async with timeout(180 if not self._247_enabled else None):
                        self.current = await self.songs.get()
                    if self.loop == 'queue' and self.current:
                        # Put the song back at the end so it plays again after the queue
                        self.songs.put_nowait(self.current)
                    source = await YTDLSource.create_source(self._ctx, self.current.source.url,
                                                            loop=self.bot.loop, ffmpeg_options=self.get_ffmpeg_options())
            except asyncio.TimeoutError:
                await self.stop()
                self.exists = False
                return
            except Exception as e:
                await self._ctx.send(embed=discord.Embed(description=f'❌ Failed to load next track: {e}',
                                                         color=discord.Color.red()))
                traceback.print_exc()
                self.current = None
                continue

            try:
                source.volume = self._volume
                self.current = Song(source)
                self._current_start_time = time.time()
                self._current_position = 0.0
                self.voice.play(self.current.source, after=self.play_next_song)
                await self.current.source.channel.send(embed=self.current.create_embed())
            except Exception as e:
                await self._ctx.send(f'An error occurred while playing: {e}')
                traceback.print_exc()
                self.play_next_song()

            await self.next.wait()

    def play_next_song(self, error=None):
        if error:
            print(f"[FFmpeg Error] {error}")
        if self.manual_stop:
            self.manual_stop = False
            return
        self.next.set()

    def skip(self):
        self.skip_votes.clear()
        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        self.songs.clear()
        self._loop_mode = 'off'
        self.manual_stop = False
        if self.audio_player and not self.audio_player.done():
            self.audio_player.cancel()
            try:
                await self.audio_player
            except asyncio.CancelledError:
                pass
        if self.voice:
            await self.voice.disconnect()
            self.voice = None

    def _skip_threshold(self):
        if not self.voice or not self.voice.channel:
            return 1
        non_bot = [m for m in self.voice.channel.members if not m.bot]
        return max(1, math.ceil(len(non_bot) / 2))

    async def restart_current(self, interaction: discord.Interaction = None, preserve_position: bool = True):
        """Recreate and restart the current track (used by effects and seek)."""
        if not self.is_playing:
            return
        elapsed = self.elapsed if preserve_position else 0.0
        self.manual_stop = True
        self.voice.stop()
        try:
            source = await YTDLSource.create_source(self._ctx, self.current.source.url,
                                                    loop=self.bot.loop,
                                                    ffmpeg_options=self.get_ffmpeg_options(seek_position=elapsed))
            source.volume = self._volume
            self.current = Song(source)
            self._current_position = elapsed
            self._current_start_time = time.time()
            self.voice.play(self.current.source, after=self.play_next_song)
            self.manual_stop = False
        except Exception as e:
            self.manual_stop = False
            raise e


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_states = {}
        self._voice_connect_locks = {}
        creds = SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
        self.sp = spotipy.Spotify(client_credentials_manager=creds)

    def _get_voice_lock(self, guild_id: int) -> asyncio.Lock:
        return self._voice_connect_locks.setdefault(guild_id, asyncio.Lock())

    def get_voice_state(self, ctx: commands.Context):
        state = self.voice_states.get(ctx.guild.id)
        if not state or not state.exists:
            state = VoiceState(self.bot, ctx)
            self.voice_states[ctx.guild.id] = state
        return state

    def cog_unload(self):
        for state in self.voice_states.values():
            self.bot.loop.create_task(state.stop())

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        await ctx.send(embed=discord.Embed(description=f'An error occurred: {str(error)}', color=discord.Color.red()))

    @staticmethod
    async def _interaction_ctx(interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        return ctx

    def _require_voice(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            raise VoiceError("You are not connected to any voice channel.")

    # ---------------- Voice Connection ----------------

    @app_commands.command(name='join', description='Joins your voice channel.')
    async def join(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You are not connected to a voice channel.", ephemeral=True)
            return

        await interaction.response.defer()
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        state._247_enabled = True
        destination = interaction.user.voice.channel
        lock = self._get_voice_lock(interaction.guild.id)

        async with lock:
            vc = interaction.guild.voice_client

            if vc and not vc.is_connected():
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
                try:
                    interaction.guild.voice_client = None
                except Exception:
                    pass
                vc = None
                await asyncio.sleep(0.5)

            if vc and vc.is_connected():
                if vc.channel.id == destination.id:
                    state.voice = vc
                    await interaction.followup.send(embed=discord.Embed(description=f'✅ Already connected to {destination}', color=discord.Color.green()))
                    return
                try:
                    await vc.move_to(destination)
                    state.voice = vc
                    await interaction.followup.send(embed=discord.Embed(description=f'➡️ Moved to {destination}', color=discord.Color.green()))
                    return
                except Exception as e:
                    print(f"[Join] move_to failed, trying fresh connect: {e}")
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)

            try:
                state.voice = await destination.connect(self_deaf=True, timeout=30.0, reconnect=True)
                await interaction.followup.send(embed=discord.Embed(description=f'🔊 Joined {destination}', color=discord.Color.green()))
            except Exception as e:
                traceback.print_exc()
                await interaction.followup.send(embed=discord.Embed(description=f'❌ Failed to join {destination}: {e}', color=discord.Color.red()))

    @app_commands.command(name='summon', description='Summons the bot to a voice channel.')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def summon(self, interaction: discord.Interaction, channel: discord.VoiceChannel = None):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not channel and not interaction.user.voice:
            await interaction.response.send_message("You are neither connected to a voice channel nor specified a channel to join.", ephemeral=True)
            return
        destination = channel or interaction.user.voice.channel
        await interaction.response.defer()
        lock = self._get_voice_lock(interaction.guild.id)
        async with lock:
            if state.voice:
                await state.voice.move_to(destination)
                await interaction.followup.send(embed=discord.Embed(description=f'➡️ Moved to {destination}', color=discord.Color.green()))
            else:
                state.voice = await destination.connect(self_deaf=True)
                await interaction.followup.send(embed=discord.Embed(description=f'🔊 Joined {destination}', color=discord.Color.green()))

    @app_commands.command(name='leave', description='Clears the queue and leaves the voice channel.')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def leave(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        await state.stop()
        self.voice_states.pop(ctx.guild.id, None)
        await interaction.response.send_message(embed=discord.Embed(description='👋 Left voice channel.', color=discord.Color.green()))

    # ---------------- Playback Control ----------------

    @app_commands.command(name='play', description='Plays a song from YouTube or Spotify.')
    @app_commands.describe(search="Song name, YouTube/video URL, YouTube playlist URL, or Spotify track/playlist/album URL")
    async def play(self, interaction: discord.Interaction, search: str):
        await self._play_core(interaction, search, mode='normal')

    @app_commands.command(name='playskip', description='Adds a song and immediately skips to it.')
    @app_commands.describe(search="Song name, YouTube/video URL, YouTube playlist URL, or Spotify track/playlist/album URL")
    async def playskip(self, interaction: discord.Interaction, search: str):
        await self._play_core(interaction, search, mode='skip')

    @app_commands.command(name='playnext', description='Adds a song to the top of the queue.')
    @app_commands.describe(search="Song name, YouTube/video URL, YouTube playlist URL, or Spotify track/playlist/album URL")
    async def playnext(self, interaction: discord.Interaction, search: str):
        await self._play_core(interaction, search, mode='next')

    async def _play_core(self, interaction: discord.Interaction, search: str, mode: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You need to be in a voice channel to play music.", ephemeral=True)
            return

        await interaction.response.defer()
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        lock = self._get_voice_lock(interaction.guild.id)

        async with lock:
            if not state.voice or not state.voice.is_connected():
                vc = interaction.guild.voice_client
                if vc and not vc.is_connected():
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                    vc = None
                if vc and vc.is_connected():
                    await vc.move_to(interaction.user.voice.channel)
                    state.voice = vc
                else:
                    state.voice = await interaction.user.voice.channel.connect(self_deaf=True)

        state.start_player()

        async def _edit_status(description: str):
            embed = discord.Embed(description=description, color=discord.Color.blurple())
            try:
                await interaction.edit_original_response(embed=embed)
            except Exception:
                # Fallback if the message has not been sent yet or is unavailable.
                await interaction.followup.send(embed=embed)

        songs = []
        try:
            ffmpeg_options = state.get_ffmpeg_options()
            search = _sanitize_search(search)
            is_playlist_input = _is_youtube_playlist(search) or (_is_spotify_url(search) and not _parse_spotify_id(search, 'track'))

            if _is_spotify_url(search):
                track_ids = await self._get_spotify_tracks(search)
                if not track_ids:
                    await _edit_status('❌ Could not extract track information from the Spotify URL.')
                    return

                if len(track_ids) > 1:
                    await _edit_status(f'🔎 Resolving **{len(track_ids)}** Spotify tracks...')

                last_update = time.time()
                for i, track_id in enumerate(track_ids):
                    try:
                        track_info = await self.bot.loop.run_in_executor(
                            None, functools.partial(self.sp.track, track_id)
                        )
                        youtube_url = await self._search_youtube(track_info)
                        if youtube_url:
                            source = await YTDLSource.create_source(ctx, youtube_url, loop=self.bot.loop, ffmpeg_options=ffmpeg_options, max_entries=1)
                            if isinstance(source, list):
                                songs.extend(Song(s) for s in source)
                            else:
                                songs.append(Song(source))
                    except Exception as e:
                        print(f"[Spotify Track Error] {e}")
                        continue

                    # Throttle progress edits to once per second.
                    if len(track_ids) > 1 and time.time() - last_update >= 1:
                        await _edit_status(f'🔎 Resolved **{len(songs)} / {len(track_ids)}** Spotify tracks...')
                        last_update = time.time()

                if not songs:
                    await _edit_status('❌ No tracks could be processed from Spotify.')
                    return
            else:
                if is_playlist_input:
                    await _edit_status('🔎 Resolving playlist, this may take a moment...')
                sources = await YTDLSource.create_source(ctx, search, loop=self.bot.loop, ffmpeg_options=ffmpeg_options)
                if not isinstance(sources, list):
                    sources = [sources]
                songs = [Song(s) for s in sources]

            if not songs:
                await _edit_status('❌ No tracks found.')
                return

            if mode == 'skip':
                for song in reversed(songs):
                    state.songs.add_to_front(song)
                state.skip()
                await _edit_status(f'⏭️ Playing **{songs[0].source.title}** now.')
            elif mode == 'next':
                for song in reversed(songs):
                    state.songs.add_to_front(song)
                if len(songs) > 1:
                    await _edit_status(f'🎶 Added **{len(songs)}** songs to the top of the queue.')
                else:
                    await _edit_status(f'🎶 Added to top: {songs[0].source}')
            else:
                if len(songs) > 1:
                    for song in songs:
                        state.songs.put_nowait(song)
                    await _edit_status(f'🎶 Enqueued **{len(songs)}** songs.')
                else:
                    state.songs.put_nowait(songs[0])
                    await _edit_status(f'🎶 Enqueued {songs[0].source}')

        except YTDLError as e:
            await _edit_status(f'❌ Error processing request: {e}')
        except Exception as e:
            traceback.print_exc()
            await _edit_status(f'❌ Unexpected error: {e}')

    @app_commands.command(name='msearch', description='Search YouTube and pick a track to play.')
    @app_commands.describe(query="What to search for")
    async def msearch(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You need to be in a voice channel to play music.", ephemeral=True)
            return
        await interaction.response.defer()
        ctx = await self._interaction_ctx(interaction)

        try:
            results = await YTDLSource.search_sources(ctx, query, loop=self.bot.loop, max_results=10)
        except YTDLError as e:
            await interaction.followup.send(embed=discord.Embed(description=f'❌ {e}', color=discord.Color.red()))
            return

        if not results:
            await interaction.followup.send(embed=discord.Embed(description='❌ No results found.', color=discord.Color.red()))
            return

        options = []
        for i, src in enumerate(results[:10], start=1):
            label = f"{i}. {src.title}"[:100]
            description = f"{src.uploader} • {src.duration}"[:100]
            options.append(discord.SelectOption(label=label, description=description, value=str(i - 1)))

        select = Select(placeholder="Choose a track...", options=options)

        async def select_callback(interaction2: discord.Interaction):
            await interaction2.response.defer()
            index = int(select.values[0])
            source = results[index]
            await self._enqueue_source(interaction2, source)
            await interaction2.followup.send(embed=discord.Embed(description=f'🎶 Selected: {source}', color=discord.Color.green()), ephemeral=True)

        select.callback = select_callback
        view = View(timeout=60)
        view.add_item(select)
        await interaction.followup.send("🔎 Search results:", view=view)

    async def _enqueue_source(self, interaction: discord.Interaction, source: YTDLSource):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You need to be in a voice channel.", ephemeral=True)
            return
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        lock = self._get_voice_lock(interaction.guild.id)
        async with lock:
            if not state.voice or not state.voice.is_connected():
                vc = interaction.guild.voice_client
                if vc and vc.is_connected():
                    await vc.move_to(interaction.user.voice.channel)
                    state.voice = vc
                else:
                    state.voice = await interaction.user.voice.channel.connect(self_deaf=True)
        state.start_player()
        state.songs.put_nowait(Song(source))

    @app_commands.command(name='now', description='Displays the currently playing song.')
    async def now(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        await interaction.response.send_message(embed=state.current.create_embed(elapsed=state.elapsed))

    @app_commands.command(name='np', description='Alias for /now.')
    async def np(self, interaction: discord.Interaction):
        await self.now(interaction)

    @app_commands.command(name='pause', description='Pauses the currently playing song.')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def pause(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if state.is_playing and state.voice.is_playing():
            state.voice.pause()
            # Keep position so we can resume accurately
            state._current_position = state.elapsed
            state._current_start_time = None
            await interaction.response.send_message(embed=discord.Embed(description='⏸️ Paused.', color=discord.Color.orange()))
        else:
            await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)

    @app_commands.command(name='resume', description='Resumes a currently paused song.')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def resume(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if state.voice and state.voice.is_paused():
            state.voice.resume()
            state._current_start_time = time.time()
            await interaction.response.send_message(embed=discord.Embed(description='▶️ Resumed.', color=discord.Color.green()))
        else:
            await interaction.response.send_message("Nothing is currently paused.", ephemeral=True)

    @app_commands.command(name='stop', description='Stops playing and clears the queue.')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def stop(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        state.songs.clear()
        if state.is_playing:
            state.voice.stop()
            await interaction.response.send_message(embed=discord.Embed(description='⏹️ Stopped and cleared queue.', color=discord.Color.red()))
        elif state.voice and state.voice.is_connected():
            await interaction.response.send_message(embed=discord.Embed(description='🧹 Queue cleared.', color=discord.Color.orange()))
        else:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)

    @app_commands.command(name='clear', description='Clears the queue but keeps the current song playing.')
    async def clear(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        state.songs.clear()
        await interaction.response.send_message(embed=discord.Embed(description='🧹 Queue cleared.', color=discord.Color.orange()))

    @app_commands.command(name='skip', description='Vote to skip the current song.')
    async def skip(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Not playing any music right now...", ephemeral=True)
            return
        voter = interaction.user
        if voter == state.current.requester:
            state.skip()
            await interaction.response.send_message(embed=discord.Embed(description='⏭️ Skipped.', color=discord.Color.green()))
        elif voter.id not in state.skip_votes:
            state.skip_votes.add(voter.id)
            total = len(state.skip_votes)
            needed = state._skip_threshold()
            if total >= needed:
                state.skip()
                await interaction.response.send_message(embed=discord.Embed(description='⏭️ Skip vote passed.', color=discord.Color.green()))
            else:
                await interaction.response.send_message(embed=discord.Embed(description=f'🗳️ Skip vote added ({total}/{needed})', color=discord.Color.orange()))
        else:
            await interaction.response.send_message("You have already voted to skip this song.", ephemeral=True)

    @app_commands.command(name='seek', description='Seeks to a position in the current track.')
    @app_commands.describe(position="Timestamp e.g. 1:30, 90, 2m")
    async def seek(self, interaction: discord.Interaction, position: str):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        try:
            pos = _parse_user_time(position)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        if state.current.source.duration_seconds and pos > state.current.source.duration_seconds:
            await interaction.response.send_message("That position is beyond the song length.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            state._current_position = pos
            state._current_start_time = time.time()
            await state.restart_current(interaction, preserve_position=True)
            await interaction.followup.send(embed=discord.Embed(description=f'⏩ Seeked to `{_format_timestamp(pos)}`.', color=discord.Color.green()))
        except Exception as e:
            await interaction.followup.send(embed=discord.Embed(description=f'❌ Seek failed: {e}', color=discord.Color.red()))

    @app_commands.command(name='forward', description='Skips forward in the current track.')
    @app_commands.describe(amount="Time to skip forward e.g. 10s, 1:00")
    async def forward(self, interaction: discord.Interaction, amount: str):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        try:
            delta = _parse_user_time(amount)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        new_pos = state.elapsed + delta
        if state.current.source.duration_seconds:
            new_pos = min(new_pos, state.current.source.duration_seconds)
        await interaction.response.defer()
        try:
            state._current_position = new_pos
            state._current_start_time = time.time()
            await state.restart_current(interaction, preserve_position=True)
            await interaction.followup.send(embed=discord.Embed(description=f'⏩ Forwarded `{_format_timestamp(delta)}`.', color=discord.Color.green()))
        except Exception as e:
            await interaction.followup.send(embed=discord.Embed(description=f'❌ Forward failed: {e}', color=discord.Color.red()))

    @app_commands.command(name='rewind', description='Rewinds in the current track.')
    @app_commands.describe(amount="Time to rewind e.g. 10s, 1:00")
    async def rewind(self, interaction: discord.Interaction, amount: str):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        try:
            delta = _parse_user_time(amount)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        new_pos = max(0.0, state.elapsed - delta)
        await interaction.response.defer()
        try:
            state._current_position = new_pos
            state._current_start_time = time.time()
            await state.restart_current(interaction, preserve_position=True)
            await interaction.followup.send(embed=discord.Embed(description=f'⏪ Rewound `{_format_timestamp(delta)}`.', color=discord.Color.green()))
        except Exception as e:
            await interaction.followup.send(embed=discord.Embed(description=f'❌ Rewind failed: {e}', color=discord.Color.red()))

    # ---------------- Queue Management ----------------

    @app_commands.command(name='queue', description='Shows the player queue.')
    async def queue(self, interaction: discord.Interaction, page: int = 1):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if len(state.songs) == 0 and not state.is_playing:
            await interaction.response.send_message("Empty queue.", ephemeral=True)
            return

        items_per_page = 10
        total = len(state.songs)
        pages = max(1, math.ceil(total / items_per_page))
        page = max(1, min(page, pages))
        start = (page - 1) * items_per_page
        end = start + items_per_page

        embed = discord.Embed(title='🎶 Music Queue', color=discord.Color.blurple())
        if state.is_playing:
            embed.add_field(name='Currently Playing',
                            value=f"[{state.current.source.title}]({state.current.source.url})\n"
                                  f"Requested by {state.current.requester.mention} • `{state.current.source.duration}`",
                            inline=False)
            embed.add_field(name='Loop Mode', value=state.loop.capitalize(), inline=True)
            embed.add_field(name='Volume', value=f"{int(state.volume * 100)}%", inline=True)

        upcoming = list(state.songs)[start:end]
        if upcoming:
            queue_text = '\n'.join(
                f'`{i + 1}.` [{song.source.title}]({song.source.url}) • {song.source.duration} • {song.requester.mention}'
                for i, song in enumerate(upcoming, start=start)
            )
            embed.add_field(name=f'Up Next ({total} total)', value=queue_text, inline=False)
        elif total == 0:
            embed.add_field(name='Up Next', value='No more songs in queue.', inline=False)

        total_duration = state.current.source.duration_seconds if state.is_playing else 0
        for song in state.songs:
            total_duration += song.source.duration_seconds or 0
        embed.set_footer(text=f'Page {page}/{pages} • Total duration: {_format_timestamp(total_duration)}')
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='shuffle', description='Shuffles the queue.')
    async def shuffle(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if len(state.songs) == 0:
            await interaction.response.send_message("Empty queue.", ephemeral=True)
            return
        state.songs.shuffle()
        await interaction.response.send_message(embed=discord.Embed(description='🔀 Queue shuffled.', color=discord.Color.green()))

    @app_commands.command(name='remove', description='Removes one or more songs from the queue.')
    async def remove(self, interaction: discord.Interaction, index: int, count: int = 1):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if len(state.songs) == 0:
            await interaction.response.send_message("Empty queue.", ephemeral=True)
            return
        if index <= 0 or index > len(state.songs):
            await interaction.response.send_message("Invalid index.", ephemeral=True)
            return
        count = max(1, min(count, len(state.songs) - index + 1))
        removed = []
        for _ in range(count):
            song = state.songs[index - 1]
            removed.append(song.source.title)
            state.songs.remove(index - 1)
        await interaction.response.send_message(embed=discord.Embed(description=f'🗑️ Removed **{len(removed)}** track(s):\n' + '\n'.join(f'• {t}' for t in removed), color=discord.Color.orange()))

    @app_commands.command(name='move', description='Moves a track to another position in the queue.')
    async def move(self, interaction: discord.Interaction, from_index: int, to_index: int):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if len(state.songs) == 0:
            await interaction.response.send_message("Empty queue.", ephemeral=True)
            return
        if from_index <= 0 or from_index > len(state.songs) or to_index <= 0 or to_index > len(state.songs):
            await interaction.response.send_message("Invalid index.", ephemeral=True)
            return
        state.songs.move(from_index - 1, to_index - 1)
        await interaction.response.send_message(embed=discord.Embed(description=f'↔️ Moved track #{from_index} to #{to_index}.', color=discord.Color.green()))

    @app_commands.command(name='jump', description='Jumps to a track in the queue.')
    async def jump(self, interaction: discord.Interaction, index: int):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        if index <= 0 or index > len(state.songs):
            await interaction.response.send_message("Invalid index.", ephemeral=True)
            return
        # Move target to front and skip current
        state.songs.move(index - 1, 0)
        state.skip()
        await interaction.response.send_message(embed=discord.Embed(description=f'⏭️ Jumped to track #{index}.', color=discord.Color.green()))

    @app_commands.command(name='loop', description='Sets the loop mode for the player.')
    @app_commands.describe(mode="Loop mode: off, track, or queue")
    @app_commands.choices(mode=[
        app_commands.Choice(name='Off', value='off'),
        app_commands.Choice(name='Track', value='track'),
        app_commands.Choice(name='Queue', value='queue')
    ])
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str] = None):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is being played at the moment.", ephemeral=True)
            return
        if mode is None:
            # Cycle through modes
            modes = ['off', 'track', 'queue']
            current_idx = modes.index(state.loop)
            new_mode = modes[(current_idx + 1) % len(modes)]
        else:
            new_mode = mode.value
        state.loop = new_mode
        emoji = {'off': '⏹️', 'track': '🔂', 'queue': '🔁'}.get(new_mode, '🔁')
        await interaction.response.send_message(embed=discord.Embed(description=f'{emoji} Loop mode set to **{new_mode}**.', color=discord.Color.green()))

    @app_commands.command(name='loopqueue', description='Toggles queue loop.')
    async def loopqueue(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is being played at the moment.", ephemeral=True)
            return
        state.loop = 'off' if state.loop == 'queue' else 'queue'
        status = "enabled" if state.loop == 'queue' else "disabled"
        await interaction.response.send_message(embed=discord.Embed(description=f'🔁 Queue loop {status}.', color=discord.Color.green()))

    @app_commands.command(name='volume', description='Sets the volume of the player (0-1000).')
    async def volume(self, interaction: discord.Interaction, volume: int):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is being played at the moment.", ephemeral=True)
            return
        if not 0 <= volume <= 1000:
            await interaction.response.send_message("Volume must be between 0 and 1000.", ephemeral=True)
            return
        state.volume = volume / 100
        await interaction.response.send_message(embed=discord.Embed(description=f'🔊 Volume set to {volume}%', color=discord.Color.green()))

    # ---------------- Audio Effects ----------------

    async def _toggle_effect(self, interaction: discord.Interaction, attr_name: str, display_name: str, mutually_exclusive: list = None):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return

        current = getattr(state, attr_name)
        setattr(state, attr_name, not current)

        if not current and mutually_exclusive:
            for other in mutually_exclusive:
                setattr(state, other, False)

        enabled = getattr(state, attr_name)
        await interaction.response.send_message(
            embed=discord.Embed(description=f'{display_name} is now {"enabled" if enabled else "disabled"}.', color=discord.Color.green())
        )

        if state.is_playing:
            try:
                await state.restart_current(interaction, preserve_position=True)
                await interaction.followup.send(embed=discord.Embed(description=f'🎚️ Applied {display_name} to current song.', color=discord.Color.green()))
            except Exception as e:
                await interaction.followup.send(embed=discord.Embed(description=f"❌ Error reapplying effect: {e}", color=discord.Color.red()))

    @app_commands.command(name='16d', description='Toggles 16D audio effect.')
    async def sixteen_d(self, interaction: discord.Interaction):
        await self._toggle_effect(interaction, '_16d_enabled', '16D audio effect')

    @app_commands.command(name='nightcore', description='Toggles nightcore mode.')
    async def nightcore(self, interaction: discord.Interaction):
        await self._toggle_effect(interaction, '_nightcore_enabled', 'Nightcore mode', ['_vaporwave_enabled'])

    @app_commands.command(name='vaporwave', description='Toggles vaporwave mode.')
    async def vaporwave(self, interaction: discord.Interaction):
        await self._toggle_effect(interaction, '_vaporwave_enabled', 'Vaporwave mode', ['_nightcore_enabled'])

    @app_commands.command(name='reverb', description='Toggles reverb effect.')
    async def reverb(self, interaction: discord.Interaction):
        await self._toggle_effect(interaction, '_reverb_enabled', 'Reverb effect')

    @app_commands.command(name='slowed', description='Toggles slowed effect.')
    async def slowed(self, interaction: discord.Interaction):
        await self._toggle_effect(interaction, '_slowed_enabled', 'Slowed effect')

    @app_commands.command(name='slownrev', description='Toggles slowed + reverb effect.')
    async def slownrev(self, interaction: discord.Interaction):
        await self._toggle_effect(interaction, '_slownrev_enabled', 'Slowed + Reverb')

    @app_commands.command(name='pitch', description='Sets the player pitch (0.5-2.0 or reset).')
    async def pitch(self, interaction: discord.Interaction, new_pitch: str):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        if new_pitch.lower() == 'reset':
            state._pitch = 1.0
            await interaction.response.send_message(embed=discord.Embed(description='Pitch reset.', color=discord.Color.green()))
        else:
            try:
                val = float(new_pitch)
                if 0.5 <= val <= 2.0:
                    state._pitch = val
                    await interaction.response.send_message(embed=discord.Embed(description=f'Pitch set to {val}.', color=discord.Color.green()))
                else:
                    await interaction.response.send_message("Pitch must be between 0.5 and 2.0.", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message("Invalid pitch value.", ephemeral=True)
                return
        if state.is_playing:
            try:
                await state.restart_current(interaction, preserve_position=True)
                await interaction.followup.send(embed=discord.Embed(description='Pitch applied to current song.', color=discord.Color.green()))
            except Exception as e:
                await interaction.followup.send(embed=discord.Embed(description=f'❌ Error applying pitch: {e}', color=discord.Color.red()))

    @app_commands.command(name='bass_boost', description='Sets bass boost in dB (-20 to 20 or reset).')
    async def bass_boost(self, interaction: discord.Interaction, amount: str):
        await self._set_bass_boost(interaction, amount)

    @app_commands.command(name='bassboost', description='Alias for /bass_boost.')
    async def bassboost(self, interaction: discord.Interaction, amount: str):
        await self._set_bass_boost(interaction, amount)

    async def _set_bass_boost(self, interaction: discord.Interaction, amount: str):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        if amount.lower() == 'reset':
            state._bass_boost = None
            await interaction.response.send_message(embed=discord.Embed(description='Bass boost disabled.', color=discord.Color.green()))
        else:
            try:
                val = int(amount)
                if -20 <= val <= 20:
                    state._bass_boost = val
                    await interaction.response.send_message(embed=discord.Embed(description=f'Bass boost set to {val} dB.', color=discord.Color.green()))
                else:
                    await interaction.response.send_message("Bass boost must be between -20 and 20 dB.", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message("Invalid value.", ephemeral=True)
                return
        if state.is_playing:
            try:
                await state.restart_current(interaction, preserve_position=True)
                await interaction.followup.send(embed=discord.Embed(description='Bass boost applied to current song.', color=discord.Color.green()))
            except Exception as e:
                await interaction.followup.send(embed=discord.Embed(description=f'❌ Error applying bass boost: {e}', color=discord.Color.red()))

    @app_commands.command(name='247', description='Toggles 24/7 mode (no auto-disconnect).')
    async def twenty_four_seven(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        state._247_enabled = not state._247_enabled
        status = "enabled" if state._247_enabled else "disabled"
        await interaction.response.send_message(embed=discord.Embed(description=f'24/7 mode is now {status}.', color=discord.Color.green()))

    @app_commands.command(name='reset_effects', description='Resets all audio effects.')
    async def reset_effects(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        state._reverb_enabled = False
        state._slowed_enabled = False
        state._16d_enabled = False
        state._nightcore_enabled = False
        state._vaporwave_enabled = False
        state._pitch = 1.0
        state._bass_boost = None
        state._slownrev_enabled = False
        await interaction.response.send_message(embed=discord.Embed(description='All audio effects reset.', color=discord.Color.green()))
        if state.is_playing:
            try:
                await state.restart_current(interaction, preserve_position=True)
                await interaction.followup.send(embed=discord.Embed(description='Default audio settings reapplied.', color=discord.Color.green()))
            except Exception as e:
                await interaction.followup.send(embed=discord.Embed(description=f'❌ Error resetting effects: {e}', color=discord.Color.red()))

    # ---------------- Spotify Helpers ----------------

    @staticmethod
    def _resolve_spotify_short_link(url: str) -> str:
        """Follow spotify.link (or similar) redirects to the real open.spotify.com URL."""
        try:
            resp = requests.head(url, allow_redirects=True, timeout=10)
            resolved = str(resp.url)
            if 'spotify.com' in resolved or resolved.startswith('spotify:'):
                return resolved
        except Exception as e:
            print(f"[Spotify Resolve Error] {e}")
        return url

    @staticmethod
    def _parse_spotify_id(url: str, kind: str) -> str | None:
        """Extract a Spotify ID from a web URL, URI, or short link."""
        if url.startswith('spotify:'):
            parts = url.split(':')
            if len(parts) >= 3 and parts[1] == kind:
                return parts[2].split('?')[0]
            return None
        # Web URL: https://open.spotify.com/<kind>/<id>?...
        match = re.search(rf'/{kind}/([^/?#]+)', url)
        return match.group(1) if match else None

    async def _get_spotify_tracks(self, url: str):
        url = _sanitize_search(url)
        if 'spotify.link' in url:
            url = await asyncio.get_event_loop().run_in_executor(None, functools.partial(self._resolve_spotify_short_link, url))

        track_ids = []
        loop = asyncio.get_event_loop()

        try:
            track_id = self._parse_spotify_id(url, 'track')
            if track_id:
                track_ids.append(track_id)
                return track_ids

            playlist_id = self._parse_spotify_id(url, 'playlist')
            if playlist_id:
                offset = 0
                limit = 100
                while True:
                    results = await loop.run_in_executor(
                        None, functools.partial(self.sp.playlist_tracks, url, limit=limit, offset=offset)
                    )
                    items = results.get('items', [])
                    if not items:
                        break
                    for item in items:
                        track = item.get('track')
                        if track and track.get('id'):
                            track_ids.append(track['id'])
                    if len(items) < limit:
                        break
                    offset += limit
                    if len(track_ids) >= 200:
                        break
                return track_ids

            album_id = self._parse_spotify_id(url, 'album')
            if album_id:
                offset = 0
                limit = 50
                while True:
                    results = await loop.run_in_executor(
                        None, functools.partial(self.sp.album_tracks, url, limit=limit, offset=offset)
                    )
                    items = results.get('items', [])
                    if not items:
                        break
                    for item in items:
                        if item.get('id'):
                            track_ids.append(item['id'])
                    if len(items) < limit:
                        break
                    offset += limit
                    if len(track_ids) >= 200:
                        break
        except Exception as e:
            print(f"[Spotify Error] {e}")
        return track_ids

    async def _search_youtube(self, track_info: dict):
        """Search YouTube for a Spotify track dict and return the best result URL."""
        try:
            name = track_info.get('name', '')
            artists = ' '.join(a.get('name', '') for a in track_info.get('artists', []))
            query = f"ytsearch:{name} {artists}".strip()
            loop = asyncio.get_event_loop()
            partial = functools.partial(YTDLSource.ytdl.extract_info, query, download=False, process=False)
            data = await loop.run_in_executor(None, partial)
            if data and 'entries' in data and data['entries']:
                entry = data['entries'][0]
                return entry.get('url') or entry.get('webpage_url') or (
                    f"https://www.youtube.com/watch?v={entry['id']}" if entry.get('id') else None
                )
            elif data and 'url' in data:
                return data['url']
        except Exception:
            pass
        return None

    # ---------------- Lyrics ----------------

    @app_commands.command(name='lyrics', description='Shows lyrics for the current song or a search query.')
    @app_commands.describe(query="Song title/artist (leave empty for current song)")
    async def lyrics(self, interaction: discord.Interaction, query: str = None):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)

        if not query:
            if not state.is_playing:
                await interaction.response.send_message("Nothing is playing. Provide a song name to search for lyrics.", ephemeral=True)
                return
            title = state.current.source.title
        else:
            title = query

        await interaction.response.defer()
        try:
            async with aiohttp.ClientSession() as session:
                # Try lyrics.ovh search endpoint first
                search_url = f"https://api.lyrics.ovh/suggest/{requests.utils.quote(title)}"
                async with session.get(search_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get('data', [])[:5]
                        if not items:
                            await interaction.followup.send(embed=discord.Embed(description='❌ No lyrics found.', color=discord.Color.red()))
                            return
                        # Use first result
                        artist = items[0]['artist']['name']
                        song_title = items[0]['title']
                        lyrics_url = f"https://api.lyrics.ovh/v1/{requests.utils.quote(artist)}/{requests.utils.quote(song_title)}"
                        async with session.get(lyrics_url, timeout=aiohttp.ClientTimeout(total=10)) as lyrics_resp:
                            if lyrics_resp.status == 200:
                                lyrics_data = await lyrics_resp.json()
                                lyrics_text = lyrics_data.get('lyrics', '')
                                if lyrics_text:
                                    await self._send_lyrics(interaction, f"{artist} - {song_title}", lyrics_text)
                                    return
            await interaction.followup.send(embed=discord.Embed(description='❌ Could not find lyrics.', color=discord.Color.red()))
        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(embed=discord.Embed(description=f'❌ Error fetching lyrics: {e}', color=discord.Color.red()))

    async def _send_lyrics(self, interaction: discord.Interaction, title: str, lyrics: str):
        chunks = [lyrics[i:i + 4000] for i in range(0, len(lyrics), 4000)]
        for idx, chunk in enumerate(chunks):
            embed = discord.Embed(title=f'🎤 Lyrics — {title}' if idx == 0 else None,
                                  description=chunk,
                                  color=discord.Color.teal())
            embed.set_footer(text=f"Part {idx + 1}/{len(chunks)}")
            if idx == 0:
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
