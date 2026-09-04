"""Music system (Groovy-inspired): YouTube/Spotify playback, queue, audio effects,
seek controls, and lyrics.

Ported 1:1 from the original bot.py (lines 899-2211). Time helpers come from
``utils.time_utils`` and the FFmpeg path from ``core.ffmpeg``.
"""

import math
import random
import asyncio
import dataclasses
import functools
import itertools
import time
import re
from collections import deque

import aiohttp
import requests
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks
from discord.ui import Button, View, Select
from async_timeout import timeout
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from zephyr.config import REDIS_URL, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
from zephyr.core.ffmpeg import FFMPEG_PATH
from zephyr.db.guild_settings import read_dj_roles
from zephyr.db.playlists import (
    PlaylistError,
    delete_playlist,
    find_playlist,
    get_playlist,
    list_playlists,
    save_playlist,
)
from zephyr.services import bridge
from zephyr.services.spotify import is_spotify_url, parse_spotify_id, resolve_short_link
from zephyr.utils.time_utils import _parse_user_time, _format_timestamp
from zephyr.core.logging import get_logger



log = get_logger(__name__)
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
    return is_spotify_url(_sanitize_search(search))


def _is_youtube_url(search: str) -> bool:
    s = _sanitize_search(search).lower()
    return _is_url(search) and ("youtube.com" in s or "youtu.be" in s or "youtube" in s)


def _is_youtube_playlist(search: str) -> bool:
    s = _sanitize_search(search).lower()
    return _is_youtube_url(search) and ("list=" in s or "/playlist" in s)


def _is_audio_file_url(search: str) -> bool:
    s = _sanitize_search(search).lower()
    return _is_url(search) and s.endswith((".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma"))


# These live in zephyr/services/spotify.py so the dashboard's playlist importer
# can use them without importing this module -- which would drag discord.py,
# yt-dlp and a voice stack into a Flask worker to parse a URL.  The module-level
# aliases are kept because _play_core and the MusicCog staticmethods below both
# reach them by these names.
_resolve_spotify_short_link = resolve_short_link
_parse_spotify_id = parse_spotify_id


def _is_spotify_playlist_input(search: str) -> bool:
    """A Spotify link that expands to many tracks: an album or a playlist."""
    return _is_spotify_url(search) and not _parse_spotify_id(_sanitize_search(search), 'track')


# How much of the queue a player snapshot carries.  The full length and duration
# are reported separately, so truncating here costs the UI nothing but honesty
# about how many entries it is showing.
SNAPSHOT_QUEUE_LIMIT = 50

# Autoplay: how many Mix entries to fetch, how many to enqueue per refill, and
# how many played tracks to remember so the radio does not loop back on itself.
AUTOPLAY_FETCH = 30
AUTOPLAY_ADD = 5
AUTOPLAY_MEMORY = 50

# How often the now-playing progress bar is redrawn.  Each tick is a message
# edit, so this is a rate-limit budget, not a smoothness setting.
NOW_PLAYING_REFRESH_SECONDS = 10
# How long to wait after the last listener leaves. Short enough not to keep a
# connection open for nothing, long enough to survive somebody hopping between
# channels -- leaving instantly would mean rejoining a second later.
EMPTY_CHANNEL_GRACE_SECONDS = 60

_VIDEO_ID_RE = re.compile(r'(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})')


def _video_id(url: str | None) -> str | None:
    """The YouTube video id in ``url``, if there is one.

    Used to seed a Mix and to key the recently-played history, so a URL with
    different tracking parameters is still recognised as the same video.
    """
    if not url:
        return None
    match = _VIDEO_ID_RE.search(url)
    return match.group(1) if match else None


class VoiceError(Exception):
    pass


def _coerce_float(value, name: str) -> float:
    """Read one number out of a bridge command's args.

    Bridge args arrive as JSON from a browser, so every one of them is untrusted:
    a missing key, a string, or a NaN must all become a message the user can act
    on rather than a TypeError in the listener.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise VoiceError(f"`{name}` must be a number.") from None
    if number != number or number in (float('inf'), float('-inf')):
        raise VoiceError(f"`{name}` must be a number.")
    return number


# Effect name -> the VoiceState attribute it toggles, and what it excludes.
_TOGGLE_EFFECTS = {
    'nightcore': ('_nightcore_enabled', ('_vaporwave_enabled',)),
    'vaporwave': ('_vaporwave_enabled', ('_nightcore_enabled',)),
    'reverb': ('_reverb_enabled', ()),
    'slowed': ('_slowed_enabled', ()),
    'slownrev': ('_slownrev_enabled', ()),
    'sixteen_d': ('_16d_enabled', ()),
}


def _apply_effects(state: 'VoiceState', args: dict) -> None:
    """Apply an effects payload from the bridge, validating every field.

    ``reset`` is handled first so a payload can clear everything and set one
    thing in the same request, which is what the UI's "reset" button does after
    the user has already moved a slider.
    """
    if args.get('reset'):
        for attribute, _ in _TOGGLE_EFFECTS.values():
            setattr(state, attribute, False)
        state._pitch = 1.0
        state._bass_boost = None

    for name, (attribute, excludes) in _TOGGLE_EFFECTS.items():
        if name not in args:
            continue
        enabled = bool(args[name])
        setattr(state, attribute, enabled)
        if enabled:
            for other in excludes:
                setattr(state, other, False)

    if 'pitch' in args:
        pitch = _coerce_float(args['pitch'], 'pitch')
        if not 0.5 <= pitch <= 2.0:
            raise VoiceError("Pitch must be between 0.5 and 2.0.")
        state._pitch = pitch

    if 'bass_boost' in args:
        if args['bass_boost'] is None:
            state._bass_boost = None
        else:
            boost = int(_coerce_float(args['bass_boost'], 'bass_boost'))
            if not -20 <= boost <= 20:
                raise VoiceError("Bass boost must be between -20 and 20 dB.")
            state._bass_boost = boost


class YTDLError(Exception):
    pass


def _format_duration(duration: int) -> str:
    """Human-readable duration. Formerly YTDLSource.parse_duration."""
    if not duration:
        return 'Unknown'
    minutes, seconds = divmod(int(duration), 60)
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


@dataclasses.dataclass(slots=True)
class Track:
    """A queue entry: metadata only.

    Deliberately holds no FFmpeg process and no discord objects.
    ``discord.FFmpegPCMAudio.__init__`` spawns its subprocess immediately, so
    building one per queue entry meant a 200-track playlist became 200 live ffmpeg
    processes -- all of them leaked, because ``audio_player_task`` re-resolves and
    re-creates the source at play time anyway.  Keeping the queue as plain data is
    the only way that arithmetic works out.

    Being plain data also makes a queue entry directly serializable, which is what
    the player snapshot and persisted playlists both need.
    """

    title: str
    # Canonical page URL, and the re-resolve key.  Optional: an imported track
    # may have only a title until the first time it is played, at which point
    # from_track fills this in and the playlist heals itself.
    url: str | None = None
    duration_seconds: int = 0
    # The requester is stored by id + mention rather than as a Member, because a
    # Member is not serializable and '<@id>' renders identically in an embed.
    requester_id: int = 0
    requester_mention: str = ''
    uploader: str = 'Unknown'
    uploader_url: str | None = None
    thumbnail: str | None = None
    upload_date: str = 'Unknown'
    source: str = 'youtube'                   # youtube | spotify | file | search
    repaired_from: str | None = None           # set when the original URL died

    @property
    def duration(self) -> str:
        return _format_duration(self.duration_seconds)

    def __str__(self):
        return f'**{self.title}** by **{self.uploader}**'

    @classmethod
    def from_info(cls, data: dict, *, requester_id: int = 0, requester_mention: str = '',
                  source: str = 'youtube') -> 'Track':
        date = data.get('upload_date')
        return cls(
            title=data.get('title') or 'Unknown',
            url=data.get('webpage_url') or data.get('url') or '',
            duration_seconds=int(data.get('duration') or 0),
            requester_id=requester_id,
            requester_mention=requester_mention,
            uploader=data.get('uploader') or data.get('channel') or 'Unknown',
            uploader_url=data.get('uploader_url'),
            thumbnail=data.get('thumbnail'),
            upload_date=f"{date[6:8]}.{date[4:6]}.{date[0:4]}" if date else 'Unknown',
            source=source,
        )

    def absorb(self, data: dict) -> None:
        """Backfill from a full extraction at play time.

        Flat playlist entries carry only id/title/duration, so thumbnails and
        uploader URLs are placeholders until the track actually plays.  Filling them
        in here means the now-playing embed is complete without paying for a full
        extraction per queue entry up front.
        """
        self.title = data.get('title') or self.title
        self.duration_seconds = int(data.get('duration') or self.duration_seconds or 0)
        self.uploader = data.get('uploader') or data.get('channel') or self.uploader
        self.uploader_url = data.get('uploader_url') or self.uploader_url
        self.thumbnail = data.get('thumbnail') or self.thumbnail
        date = data.get('upload_date')
        if date:
            self.upload_date = f"{date[6:8]}.{date[4:6]}.{date[0:4]}"

    def create_embed(self, elapsed: float = None) -> discord.Embed:
        embed = discord.Embed(title='🎵 Now playing',
                              description=f'```css\n{self.title}\n```',
                              color=discord.Color.blurple())
        if elapsed is not None and self.duration_seconds > 0:
            elapsed = max(0.0, min(elapsed, self.duration_seconds))
            bar = self._progress_bar(elapsed, self.duration_seconds)
            embed.add_field(name='Progress',
                            value=f"`{_format_timestamp(elapsed)} / {_format_timestamp(self.duration_seconds)}`\n{bar}",
                            inline=False)
        embed.add_field(name='Duration', value=self.duration, inline=True)
        embed.add_field(name='Requested by', value=self.requester_mention or 'Unknown', inline=True)
        embed.add_field(name='Uploader', value=f'[{self.uploader}]({self.uploader_url})', inline=True)
        embed.add_field(name='URL', value=f'[Click]({self.url})', inline=True)
        if self.thumbnail:
            embed.set_thumbnail(url=self.thumbnail)
        if self.repaired_from:
            embed.set_footer(text='Original video unavailable — playing a re-resolved match.')
        return embed

    def to_payload(self) -> dict:
        """The wire form, for a player snapshot or a stored playlist row.

        ``dataclasses.asdict`` would work but would also leak every future field
        straight to the browser; an explicit projection is the boundary.  Ids are
        strings because snowflakes exceed JavaScript's safe integer range.
        """
        return {
            'title': self.title,
            'url': self.url,
            'duration_s': self.duration_seconds,
            'requester_id': str(self.requester_id) if self.requester_id else None,
            'requester_mention': self.requester_mention,
            'uploader': self.uploader,
            'thumbnail': self.thumbnail,
            'source': self.source,
        }

    @staticmethod
    def _progress_bar(elapsed: float, total: int, length: int = 15) -> str:
        ratio = elapsed / total if total else 0
        ratio = max(0.0, min(1.0, ratio))
        filled = int(ratio * (length - 1))
        bar = '▬' * filled + '🔘' + '▬' * (length - filled - 1)
        return bar


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

    def __init__(self, source: discord.FFmpegPCMAudio, *, data: dict, volume: float = 0.5):
        super().__init__(source, volume)
        self.data = data
        self.stream_url = data.get('url')

    # Kept as an alias so nothing that referenced the old classmethod breaks.
    parse_duration = staticmethod(_format_duration)

    @staticmethod
    def _entry_url(entry: dict) -> str | None:
        """Best-effort canonical URL from a flat yt-dlp entry."""
        if not entry:
            return None
        url = entry.get('url') or entry.get('webpage_url')
        if not url and entry.get('id'):
            url = f"https://www.youtube.com/watch?v={entry['id']}"
        return url

    @classmethod
    async def _extract(cls, query: str, *, loop, process: bool = True, timeout_s: int = 15):
        partial = functools.partial(cls.ytdl.extract_info, query, download=False, process=process)
        if process:
            return await asyncio.wait_for(loop.run_in_executor(None, partial), timeout=timeout_s)
        return await loop.run_in_executor(None, partial)

    @classmethod
    async def resolve_tracks(cls, search: str, *, requester_id: int = 0, requester_mention: str = '',
                             loop: asyncio.AbstractEventLoop = None, max_entries: int = 200) -> list['Track']:
        """Resolve a query into Track metadata. Always a list, never a bare object.

        The old ``create_source`` returned either one source or a list, so every
        caller had to branch on the type -- and ``audio_player_task`` did not, which
        meant a URL yt-dlp re-resolved as a playlist reached ``source.volume = ...``
        with a list and wedged the guild.

        Nothing here constructs an FFmpeg source; see ``from_track``.
        """
        loop = loop or asyncio.get_event_loop()
        search = _sanitize_search(search)
        as_url = _is_url(search)

        # A direct audio file needs no yt-dlp round trip at all: FFmpeg can read the
        # URL itself. _is_audio_file_url was written for this and had no caller.
        if _is_audio_file_url(search):
            name = search.rsplit('/', 1)[-1].split('?')[0] or search
            return [Track(title=name, url=search, requester_id=requester_id,
                          requester_mention=requester_mention, source='file')]

        # Route plain text through YouTube search explicitly: recent yt-dlp/YouTube
        # changes make default_search 'auto' return a generic extractor with no entries.
        query = search if as_url else f"ytsearch10:{search}"

        try:
            data = await cls._extract(query, loop=loop, process=False)
            if data is None:
                raise YTDLError(f'Could not find anything that matches `{search}`')

            entries = list(data.get('entries') or [])

            # Plain-text search: take only the top hit rather than enqueueing ten.
            if not as_url:
                if not entries:
                    raise YTDLError(f'Could not find anything that matches `{search}`')
                tracks = cls._build_tracks_from_entries(
                    entries[:1], requester_id=requester_id, requester_mention=requester_mention, source='search')
                if not tracks:
                    raise YTDLError(f'Could not find anything that matches `{search}`')
                return tracks

            # A single URL with no entries: one flat extract is enough for metadata.
            if not entries:
                url = data.get('webpage_url') or data.get('url') or search
                return [Track.from_info({**data, 'webpage_url': url},
                                        requester_id=requester_id,
                                        requester_mention=requester_mention)]

            if max_entries:
                entries = entries[:max_entries]
            tracks = cls._build_tracks_from_entries(
                entries, requester_id=requester_id, requester_mention=requester_mention)
            if not tracks:
                raise YTDLError('Could not resolve any tracks from the playlist. '
                                'All entries were unavailable or unsupported.')
            return tracks

        except asyncio.TimeoutError:
            raise YTDLError(f'Request timed out while processing `{search}`. Try a shorter query or playlist.')
        except YTDLError:
            raise
        except Exception as e:
            log.exception("Could not resolve tracks for %r", search)
            raise YTDLError(f"Failed to process `{search}`: {e}")

    @classmethod
    def _build_tracks_from_entries(cls, entries: list, *, requester_id: int = 0,
                                   requester_mention: str = '', source: str = 'youtube') -> list['Track']:
        """Turn flat yt-dlp entries into Tracks with **no** per-entry extraction.

        A flat (``process=False``) extract already carries id, title and duration for
        every entry, so a 200-track playlist costs one network call instead of 201 --
        and zero subprocesses instead of 200.  Entries with no usable URL are dropped;
        anything that turns out to be dead is caught at play time by ``from_track``,
        which is where the URL is actually used.
        """
        tracks = []
        skipped = 0
        for entry in entries:
            url = cls._entry_url(entry)
            if not url:
                skipped += 1
                continue
            tracks.append(Track.from_info({**entry, 'webpage_url': url},
                                          requester_id=requester_id,
                                          requester_mention=requester_mention,
                                          source=source))
        if skipped:
            log.info("Added %d tracks, skipped %d with no usable URL", len(tracks), skipped)
        return tracks

    @classmethod
    async def search_tracks(cls, search: str, *, requester_id: int = 0, requester_mention: str = '',
                            loop: asyncio.AbstractEventLoop = None, max_results: int = 10) -> list['Track']:
        """Search results for /msearch.

        Previously this made one extraction *and one FFmpeg process* per result, then
        threw nine of them away -- so every /msearch leaked nine subprocesses. One
        flat extract now covers all ten.
        """
        loop = loop or asyncio.get_event_loop()
        search = _sanitize_search(search)
        try:
            data = await cls._extract(f"ytsearch{max_results}:{search}", loop=loop, process=False)
            if data is None or not data.get('entries'):
                return []
            return cls._build_tracks_from_entries(list(data['entries']),
                                                  requester_id=requester_id,
                                                  requester_mention=requester_mention,
                                                  source='search')
        except Exception as e:
            log.exception("Could not search for %r", search)
            raise YTDLError(f"Failed to search `{search}`: {e}")

    @classmethod
    async def from_track(cls, track: 'Track', *, ffmpeg_options: dict = None, volume: float = 0.5,
                         seek: float = None, loop: asyncio.AbstractEventLoop = None) -> 'YTDLSource':
        """Build the playable source for a Track. The only FFmpeg construction site.

        Stream URLs expire, so this always re-extracts -- which the old code did too,
        immediately after having built a source per queue entry.

        Two paths reach the by-title search: a track that never had a URL (a
        Spotify import stores metadata only, so resolution is deferred to exactly
        here, once, for the tracks actually played), and a track whose URL has
        since died (the reason saved playlists do not rot).  A *timeout* is
        deliberately not retried: a network stall will stall a search too.
        """
        loop = loop or asyncio.get_event_loop()
        options = dict(ffmpeg_options or cls.DEFAULT_FFMPEG_OPTIONS)
        if seek:
            before = options.get('before_options', '')
            options['before_options'] = f"{before} -ss {seek}".strip()

        if track.source == 'file':
            # Direct media: hand the URL straight to FFmpeg, no extraction.
            return cls(discord.FFmpegPCMAudio(track.url, executable=FFMPEG_PATH, **options),
                       data={'url': track.url, 'title': track.title}, volume=volume)

        info = None
        if track.url:
            try:
                info = await cls._extract(track.url, loop=loop)
            except asyncio.TimeoutError:
                raise
            except Exception as exc:
                if not track.title or track.title == track.url:
                    raise YTDLError(f'Could not fetch `{track.url}`: {exc}')
                log.info("Re-resolving %s after a failure (%s); searching for %r", track.url, exc, track.title)
        elif not track.title:
            raise YTDLError('That track has neither a link nor a title.')

        if not info or not info.get('url'):
            info = await cls._resolve_by_title(track, loop=loop)

        if not info or 'url' not in info:
            raise YTDLError(f'Could not fetch `{track.title or track.url}`')
        # A flat entry's title/duration are placeholders until now.
        track.absorb(info)
        return cls(discord.FFmpegPCMAudio(info['url'], executable=FFMPEG_PATH, **options),
                   data=info, volume=volume)

    @classmethod
    async def _resolve_by_title(cls, track: 'Track', *, loop) -> dict | None:
        """Find a playable video for a track that has only a title.

        The track is mutated with what was found, so a saved playlist heals
        itself: the next play uses the repaired URL directly instead of searching
        again.  ``repaired_from`` is only set when there was something to repair,
        which keeps the "playing a re-resolved match" footer off imports that
        never had a URL in the first place.
        """
        found = await cls._extract(f"ytsearch1:{track.title}", loop=loop, process=False)
        for entry in list((found or {}).get('entries') or []):
            url = cls._entry_url(entry)
            if not url:
                continue
            info = await cls._extract(url, loop=loop)
            if info and info.get('url'):
                if track.url:
                    track.repaired_from = track.url
                track.url = info.get('webpage_url') or url
                return info
            break
        raise YTDLError(f'`{track.title}` is not available.')


class NowPlayingView(View):
    """Transport controls under the now-playing embed.

    Every button routes through the cog's bridge handler rather than
    reimplementing the action, so the Discord buttons and the web remote cannot
    drift apart -- including the permission check, which lives in
    ``MusicCog._authorize`` and is applied identically to both.

    ``timeout=None`` because the message outlives any interaction, and the view
    is explicitly disabled when playback ends rather than expiring silently and
    leaving buttons that look live and do nothing.
    """

    def __init__(self, cog: 'MusicCog', guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = int(guild_id)

    async def _run(self, interaction: discord.Interaction, action: str, args: dict | None = None):
        handler = self.cog.bridge_actions().get(action)
        try:
            await handler(interaction.guild, interaction.user.id, args or {})
        except (VoiceError, YTDLError) as exc:
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True)
            return
        except Exception as exc:
            log.exception("A now-playing button failed")
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True)
            return
        # The refresh loop redraws the embed within a few seconds; acknowledging
        # silently keeps the channel free of one message per button press.
        await interaction.response.defer()

    @discord.ui.button(emoji='⏯️', style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction: discord.Interaction, button: Button):
        state = self.cog.peek_voice_state(self.guild_id)
        paused = bool(state and state.voice and state.voice.is_paused())
        await self._run(interaction, 'player.resume' if paused else 'player.pause')

    @discord.ui.button(emoji='⏭️', style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: Button):
        await self._run(interaction, 'player.skip')

    @discord.ui.button(emoji='🔁', style=discord.ButtonStyle.secondary)
    async def loop_mode(self, interaction: discord.Interaction, button: Button):
        state = self.cog.peek_voice_state(self.guild_id)
        order = ['off', 'track', 'queue']
        current = state.loop if state else 'off'
        following = order[(order.index(current) + 1) % len(order)] if current in order else 'off'
        await self._run(interaction, 'player.loop', {'mode': following})

    @discord.ui.button(emoji='🔀', style=discord.ButtonStyle.secondary)
    async def shuffle(self, interaction: discord.Interaction, button: Button):
        await self._run(interaction, 'player.shuffle')

    @discord.ui.button(emoji='⏹️', style=discord.ButtonStyle.danger)
    async def stop_playback(self, interaction: discord.Interaction, button: Button):
        await self._run(interaction, 'player.stop')


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
        # Shuffle a list, not the deque: random.shuffle does O(n) random access, which
        # is O(n) per element on a deque and so O(n^2) overall -- noticeable on a
        # few hundred tracks.
        items = list(self._queue)
        random.shuffle(items)
        self._queue = deque(items)

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
                await self._notify(embed=discord.Embed(description=f'❌ Failed to load next track: {e}',
                                                       color=discord.Color.red()))
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
        if not self.voice or not self.voice.channel:
            return 1
        non_bot = [m for m in self.voice.channel.members if not m.bot]
        return max(1, math.ceil(len(non_bot) / 2))

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


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_states = {}
        self._voice_connect_locks = {}
        # guild id -> DJ role id.  Absent means "no DJ role configured", which is
        # a different rule rather than a stricter one; see _authorize.
        self._dj_role_ids: dict[int, str] = {}
        # guild_id -> the pending "everyone left" timer, so a rejoin can cancel it.
        self._empty_timers: dict[int, asyncio.Task] = {}
        creds = SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
        self.sp = spotipy.Spotify(client_credentials_manager=creds)

    def _get_voice_lock(self, guild_id: int) -> asyncio.Lock:
        return self._voice_connect_locks.setdefault(guild_id, asyncio.Lock())

    def peek_voice_state(self, guild_id: int) -> VoiceState | None:
        """The live state for a guild, or None. Never creates one.

        Callers with no Context (the Redis bridge) need to be able to answer
        "nothing is playing" rather than silently spinning up a state.
        """
        state = self.voice_states.get(int(guild_id))
        return state if state and state.exists else None

    def ensure_voice_state(self, guild_id: int, *, channel_id: int | None = None) -> VoiceState:
        """Get or create the state for a guild, refreshing the sticky channel."""
        state = self.voice_states.get(int(guild_id))
        if not state or not state.exists:
            state = VoiceState(self.bot, guild_id, channel_id=channel_id)
            state.on_change = self.publish_state
            state.np_view_factory = functools.partial(NowPlayingView, self, int(guild_id))
            self.voice_states[int(guild_id)] = state
        elif channel_id is not None:
            state.np_channel_id = channel_id
        return state

    async def teardown_voice_state(self, guild_id: int) -> None:
        """Stop playback and forget the guild's state entirely.

        `stop()` alone leaves the entry in `self.voice_states`. That mattered
        because three callers popped the dict by hand -- /leave, TTS's
        /disconnect, and cog_unload -- and every other path did not, so the
        dict accumulated dead states for the life of the process. One method
        now owns both halves, and the snapshot is cleared so the dashboard stops
        showing a player for a guild the bot has left.
        """
        state = self.voice_states.pop(int(guild_id), None)
        if state is None:
            return
        try:
            await state.stop()
        except Exception:
            log.exception("Could not stop the player for %s during teardown", guild_id)
        await self._clear_snapshot(int(guild_id))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """React to the channel emptying, and to being removed from it.

        There was no listener of any kind in this package. The 180s idle timeout
        is armed by `async_timeout` around the *queue read* inside
        audio_player_task, so it only ever starts once the queue runs dry -- a
        long track playing to an empty channel never arms it at all, and the bot
        would keep streaming to nobody until the track ended.

        Two cases:

        * The bot itself left or was moved. `after.channel is None` means a
          moderator disconnected it, and without this the state stayed in the
          dict with `exists` True, so the dashboard kept showing a player and
          the next /play reused a state whose voice client was gone.
        * The last human left. Pause immediately (there is no point decoding
          audio for an empty room) and start a short grace timer rather than
          leaving at once, because the common case is somebody hopping between
          channels for a few seconds.
        """
        guild = member.guild
        state = self.peek_voice_state(guild.id)
        if state is None:
            return

        if member.id == self.bot.user.id:
            if after.channel is None:
                log.info("Disconnected from voice in %s; tearing down the player", guild.id)
                await self.teardown_voice_state(guild.id)
            return

        channel = state.voice.channel if state.voice else None
        if channel is None:
            return

        # Only movements involving the bot's own channel can change the answer.
        if before.channel != channel and after.channel != channel:
            return

        listeners = [m for m in channel.members if not m.bot]
        if listeners:
            self._cancel_empty_timer(guild.id)
            return

        if state.voice.is_playing():
            state.voice.pause()
        self._start_empty_timer(guild.id, channel)

    def _cancel_empty_timer(self, guild_id: int) -> None:
        timer = self._empty_timers.pop(int(guild_id), None)
        if timer and not timer.done():
            timer.cancel()

    def _start_empty_timer(self, guild_id: int, channel) -> None:
        self._cancel_empty_timer(guild_id)
        self._empty_timers[int(guild_id)] = asyncio.create_task(self._leave_if_still_empty(guild_id, channel))

    async def _leave_if_still_empty(self, guild_id: int, channel) -> None:
        """Wait out the grace period, then leave if nobody came back."""
        try:
            await asyncio.sleep(EMPTY_CHANNEL_GRACE_SECONDS)
            state = self.peek_voice_state(guild_id)
            if state is None or not state.voice:
                return
            if any(not m.bot for m in state.voice.channel.members):
                return
            # Announced, unlike the idle timeout, which says nothing at all --
            # coming back to a stopped player with no explanation reads as a
            # crash.
            await state._notify(embed=discord.Embed(
                description=f'👋 Left {channel} — everyone had gone.',
                color=discord.Color.blue(),
            ))
            await self.teardown_voice_state(guild_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Could not leave the empty channel in %s", guild_id)
        finally:
            self._empty_timers.pop(int(guild_id), None)

    def get_voice_state(self, ctx: commands.Context):
        """Context-flavoured wrapper, so the existing command call sites are unchanged."""
        channel_id = ctx.channel.id if getattr(ctx, 'channel', None) else None
        return self.ensure_voice_state(ctx.guild.id, channel_id=channel_id)

    def cog_unload(self):
        self._snapshot_loop.cancel()
        self._settings_loop.cancel()
        self._now_playing_loop.cancel()
        for timer in list(self._empty_timers.values()):
            if not timer.done():
                timer.cancel()
        # One task per guild rather than two loops doing half the job each:
        # teardown_voice_state stops the player, forgets the state and clears
        # the snapshot together.
        for guild_id in list(self.voice_states):
            self.bot.loop.create_task(self.teardown_voice_state(guild_id))

    # ---------------- Web bridge ----------------

    async def cog_load(self):
        # The DJ role is read whether or not the dashboard exists -- it governs
        # the in-Discord now-playing buttons too.  Only the snapshot publishing
        # is conditional on there being somewhere to publish to.
        self._settings_loop.start()
        self._now_playing_loop.start()
        if REDIS_URL:
            self._snapshot_loop.start()

    async def _clear_snapshot(self, guild_id: int) -> None:
        if not REDIS_URL:
            return
        try:
            await asyncio.to_thread(bridge.clear_player_snapshot, guild_id)
        except Exception as exc:
            log.exception("Could not clear the player snapshot for %s", guild_id)

    async def publish_state(self, guild_id: int) -> None:
        """Push one guild's snapshot to Redis now.

        Called after every bridge action and at each playback transition, so a
        change made from the browser is visible on the next poll rather than on
        the next tick of the periodic loop.  Off the event loop via to_thread
        because redis-py is synchronous.  Failures are logged and swallowed: the
        dashboard going stale must never break playback.
        """
        if not REDIS_URL:
            return
        state = self.peek_voice_state(guild_id)
        if state is None:
            await self._clear_snapshot(guild_id)
            return
        try:
            await asyncio.to_thread(bridge.write_player_snapshot, guild_id, state.snapshot())
        except Exception as exc:
            log.exception("Could not publish the player snapshot for %s", guild_id)

    @tasks.loop(seconds=3)
    async def _snapshot_loop(self):
        """Keep the snapshot fresh for changes made in Discord.

        Three seconds to match the dashboard's poll interval; a slower loop would
        make a /volume typed in Discord take up to two polls to appear.  Only
        guilds with a live state are published -- an idle bot writes nothing.
        """
        for guild_id in list(self.voice_states):
            if self.peek_voice_state(guild_id) is not None:
                await self.publish_state(guild_id)

    @_snapshot_loop.before_loop
    async def _before_snapshot_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=NOW_PLAYING_REFRESH_SECONDS)
    async def _now_playing_loop(self):
        """Advance the progress bar on every live now-playing message.

        Ten seconds, not one: each tick is a message edit, and Discord's
        per-channel edit budget is small enough that a per-second bar would spend
        it all on a cosmetic detail.  Paused players are skipped -- the bar is
        not moving, so an edit would rewrite the message with identical content.
        """
        for guild_id in list(self.voice_states):
            state = self.peek_voice_state(guild_id)
            if state and state.np_message and state.voice and state.voice.is_playing():
                await state.refresh_now_playing()

    @_now_playing_loop.before_loop
    async def _before_now_playing_loop(self):
        await self.bot.wait_until_ready()

    def bridge_actions(self) -> dict:
        """The actions this cog serves on the web bridge.

        Discovered by ``ZephyrBot`` rather than registered with it, so a cog that
        wants a bridge endpoint only has to grow this method.

        Every mutating action republishes the snapshot before answering, so the
        browser's next poll shows the result of its own click instead of state
        from up to one loop tick ago.
        """
        mutating = {
            'player.pause': self._bridge_pause,
            'player.resume': self._bridge_resume,
            'player.skip': self._bridge_skip,
            'player.stop': self._bridge_stop,
            'player.clear': self._bridge_clear,
            'player.shuffle': self._bridge_shuffle,
            'player.seek': self._bridge_seek,
            'player.volume': self._bridge_volume,
            'player.loop': self._bridge_loop,
            'player.jump': self._bridge_jump,
            'player.remove': self._bridge_remove,
            'player.move': self._bridge_move,
            'player.play': self._bridge_play,
            'player.effects': self._bridge_effects,
            'player.autoplay': self._bridge_autoplay,
            'playlist.load': self._bridge_playlist_load,
        }
        actions = {name: self._publishing(handler) for name, handler in mutating.items()}
        actions['player.state'] = self._bridge_state
        actions['settings.reload'] = self._bridge_reload_settings
        return actions

    def _publishing(self, handler):
        async def wrapper(guild, actor_id, args):
            result = await handler(guild, actor_id, args)
            await self.publish_state(guild.id)
            return result

        return wrapper

    # The bot is the authority on permissions (see zephyr/services/bridge.py).
    # Everything below re-derives them from the live Discord cache; whatever the
    # web tier believed when it rendered a button is irrelevant here.

    def _authorize(self, guild: discord.Guild, actor_id, *, require_voice: bool = True) -> discord.Member:
        """Resolve the actor and confirm they may drive this guild's player.

        The rule is one sentence so it can be explained: **if a DJ role is
        configured you need it (or Manage Server); if one is not, you need to be
        in the voice channel the bot is in.**  Anything that changes what other
        people are hearing goes through here.
        """
        if guild is None:
            raise VoiceError("That server is not available.")
        try:
            member = guild.get_member(int(actor_id))
        except (TypeError, ValueError):
            member = None
        if member is None:
            # Not cached is treated as not present.  Fetching would let an
            # unauthenticated id force an API call per command.
            raise VoiceError("You are not a member of that server.")
        if member.guild_permissions.manage_guild:
            return member

        dj_role_id = self._dj_role_ids.get(int(guild.id))
        if dj_role_id:
            if any(str(role.id) == str(dj_role_id) for role in member.roles):
                return member
            raise VoiceError("You need the DJ role to control the player.")

        if require_voice:
            state = self.peek_voice_state(guild.id)
            bot_channel = state.voice.channel if state and state.voice and state.voice.is_connected() else None
            listening = member.voice.channel if member.voice else None
            if bot_channel is not None and listening != bot_channel:
                raise VoiceError("Join the voice channel Zephyr is in to control the player.")
            if bot_channel is None and listening is None:
                raise VoiceError("You are not connected to a voice channel.")
        return member

    async def reload_dj_roles(self) -> None:
        """Refresh the DJ-role cache from the database.

        Cached rather than read per command: ``_authorize`` runs on every button
        press and every bridge action, and a database round trip on that path
        would be paid constantly to answer a question that changes roughly never.
        The dashboard calls ``settings.reload`` after a save, so the cache is
        corrected immediately rather than at the next slow tick.
        """
        try:
            self._dj_role_ids = {
                int(guild_id): role_id
                for guild_id, role_id in (await asyncio.to_thread(read_dj_roles)).items()
            }
        except Exception as exc:
            log.exception("Could not read DJ roles")

    @tasks.loop(minutes=10)
    async def _settings_loop(self):
        await self.reload_dj_roles()

    @_settings_loop.before_loop
    async def _before_settings_loop(self):
        await self.bot.wait_until_ready()

    async def _bridge_reload_settings(self, guild, actor_id, args):
        await self.reload_dj_roles()
        return {'reloaded': True}

    def _require_state(self, guild_id) -> VoiceState:
        state = self.peek_voice_state(guild_id)
        if state is None or not state.voice or not state.voice.is_connected():
            raise VoiceError("Zephyr is not connected to a voice channel.")
        return state

    async def _bridge_state(self, guild, actor_id, args):
        state = self.peek_voice_state(guild.id) if guild else None
        return state.snapshot() if state else {'guild_id': str(guild.id), 'connected': False}

    async def _bridge_pause(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        if not state.voice.is_playing():
            raise VoiceError("Nothing is playing.")
        state.voice.pause()
        state._current_position = state.elapsed
        state._current_start_time = None
        return {'paused': True}

    async def _bridge_resume(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        if not state.voice.is_paused():
            raise VoiceError("Nothing is paused.")
        state.voice.resume()
        state._current_start_time = time.time()
        return {'paused': False}

    async def _bridge_skip(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        if not state.is_playing:
            raise VoiceError("Nothing is playing.")
        skipped = state.current.title
        state.skip()
        return {'skipped': skipped}

    async def _bridge_stop(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        state.songs.clear()
        if state.is_playing:
            state.voice.stop()
        return {'stopped': True}

    async def _bridge_clear(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        removed = len(state.songs)
        state.songs.clear()
        return {'removed': removed}

    async def _bridge_shuffle(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        state.songs.shuffle()
        return {'queue_length': len(state.songs)}

    async def _bridge_seek(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        if not state.is_playing:
            raise VoiceError("Nothing is playing.")
        position = _coerce_float(args.get('position'), 'position')
        if state.current.duration_seconds and position > state.current.duration_seconds:
            raise VoiceError("That position is beyond the song length.")
        state._current_position = max(0.0, position)
        state._current_start_time = time.time()
        await state.restart_current(preserve_position=True)
        return {'position_s': state._current_position}

    async def _bridge_volume(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        volume = int(_coerce_float(args.get('volume'), 'volume'))
        if not 0 <= volume <= 1000:
            raise VoiceError("Volume must be between 0 and 1000.")
        state.volume = volume / 100
        return {'volume': volume}

    async def _bridge_loop(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        mode = str(args.get('mode') or '').lower()
        if mode not in {'off', 'track', 'queue'}:
            raise VoiceError("Loop mode must be off, track or queue.")
        state.loop = mode
        return {'loop': mode}

    async def _bridge_jump(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        index = int(_coerce_float(args.get('index'), 'index'))
        if not 0 <= index < len(state.songs):
            raise VoiceError("That track is not in the queue.")
        state.songs.move(index, 0)
        state.skip()
        return {'jumped_to': index}

    async def _bridge_remove(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        index = int(_coerce_float(args.get('index'), 'index'))
        if not 0 <= index < len(state.songs):
            raise VoiceError("That track is not in the queue.")
        removed = state.songs[index].title
        state.songs.remove(index)
        return {'removed': removed}

    async def _bridge_move(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        source = int(_coerce_float(args.get('from'), 'from'))
        target = int(_coerce_float(args.get('to'), 'to'))
        try:
            state.songs.move(source, target)
        except IndexError:
            raise VoiceError("That track is not in the queue.") from None
        return {'from': source, 'to': target}

    async def _bridge_effects(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        _apply_effects(state, args)
        if state.is_playing:
            await state.restart_current(preserve_position=True)
        return {'effects': state.effects()}

    async def _bridge_play(self, guild, actor_id, args):
        """Enqueue from the browser.

        The actor's own voice channel is where the bot goes -- deliberately not a
        channel id from the request, which would let anyone with a session pull
        the bot into a channel they cannot see.
        """
        member = self._authorize(guild, actor_id)
        query = str(args.get('query') or '').strip()
        if not query:
            raise VoiceError("Nothing to play.")
        destination = member.voice.channel if member.voice else None
        state = self.ensure_voice_state(guild.id)
        if state.voice and state.voice.is_connected():
            destination = state.voice.channel
        if destination is None:
            raise VoiceError("Join a voice channel first.")

        async with self._get_voice_lock(guild.id):
            if not state.voice or not state.voice.is_connected():
                state.voice = await destination.connect(self_deaf=True)
        state.start_player()

        tracks = await YTDLSource.resolve_tracks(
            query, requester_id=member.id, requester_mention=member.mention, loop=self.bot.loop)
        if not tracks:
            raise VoiceError("Nothing found for that query.")
        if str(args.get('mode')) == 'next':
            for track in reversed(tracks):
                state.songs.add_to_front(track)
        else:
            for track in tracks:
                state.songs.put_nowait(track)
        return {'added': len(tracks), 'title': tracks[0].title}

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
        # /join deliberately does NOT enable 24/7: it used to set _247_enabled here,
        # which disabled the idle timeout permanently for any guild that ever ran the
        # command, so the bot sat in an empty channel forever.  Use /247 for that.
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
                    log.warning("move_to failed, trying a fresh connect: %s", e)
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)

            try:
                state.voice = await destination.connect(self_deaf=True, timeout=30.0, reconnect=True)
                await interaction.followup.send(embed=discord.Embed(description=f'🔊 Joined {destination}', color=discord.Color.green()))
            except Exception as e:
                log.exception("Could not join a voice channel")
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
        await self.teardown_voice_state(ctx.guild.id)
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
        requester_id = interaction.user.id
        requester_mention = interaction.user.mention
        try:
            # No ffmpeg_options here any more: the filter chain is applied when the
            # source is built at play time, so effects toggled while a track sits in
            # the queue now take effect on it rather than being baked in at enqueue.
            search = _sanitize_search(search)
            is_playlist_input = _is_youtube_playlist(search) or _is_spotify_playlist_input(search)

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
                            songs.extend(await YTDLSource.resolve_tracks(
                                youtube_url, requester_id=requester_id, requester_mention=requester_mention,
                                loop=self.bot.loop, max_entries=1))
                    except Exception as e:
                        log.exception("Could not resolve a Spotify track")
                        continue

                    # Throttle progress edits to once per second.
                    if len(track_ids) > 1 and time.time() - last_update >= 1:
                        await _edit_status(f'🔎 Resolved **{len(songs)} / {len(track_ids)}** Spotify tracks...')
                        last_update = time.time()

                for track in songs:
                    track.source = 'spotify'

                if not songs:
                    await _edit_status('❌ No tracks could be processed from Spotify.')
                    return
            else:
                if is_playlist_input:
                    await _edit_status('🔎 Resolving playlist, this may take a moment...')
                songs = await YTDLSource.resolve_tracks(
                    search, requester_id=requester_id, requester_mention=requester_mention, loop=self.bot.loop)

            if not songs:
                await _edit_status('❌ No tracks found.')
                return

            if mode == 'skip':
                for song in reversed(songs):
                    state.songs.add_to_front(song)
                state.skip()
                await _edit_status(f'⏭️ Playing **{songs[0].title}** now.')
            elif mode == 'next':
                for song in reversed(songs):
                    state.songs.add_to_front(song)
                if len(songs) > 1:
                    await _edit_status(f'🎶 Added **{len(songs)}** songs to the top of the queue.')
                else:
                    await _edit_status(f'🎶 Added to top: {songs[0]}')
            else:
                if len(songs) > 1:
                    for song in songs:
                        state.songs.put_nowait(song)
                    await _edit_status(f'🎶 Enqueued **{len(songs)}** songs.')
                else:
                    state.songs.put_nowait(songs[0])
                    await _edit_status(f'🎶 Enqueued {songs[0]}')

        except YTDLError as e:
            await _edit_status(f'❌ Error processing request: {e}')
        except Exception as e:
            log.exception("Could not process a play request")
            await _edit_status(f'❌ Unexpected error: {e}')

    @app_commands.command(name='msearch', description='Search YouTube and pick a track to play.')
    @app_commands.describe(query="What to search for")
    async def msearch(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You need to be in a voice channel to play music.", ephemeral=True)
            return
        await interaction.response.defer()

        try:
            results = await YTDLSource.search_tracks(query, requester_id=interaction.user.id,
                                                     requester_mention=interaction.user.mention,
                                                     loop=self.bot.loop, max_results=10)
        except YTDLError as e:
            await interaction.followup.send(embed=discord.Embed(description=f'❌ {e}', color=discord.Color.red()))
            return

        if not results:
            await interaction.followup.send(embed=discord.Embed(description='❌ No results found.', color=discord.Color.red()))
            return

        options = []
        for i, track in enumerate(results[:10], start=1):
            label = f"{i}. {track.title}"[:100]
            description = f"{track.uploader} • {track.duration}"[:100]
            options.append(discord.SelectOption(label=label, description=description, value=str(i - 1)))

        select = Select(placeholder="Choose a track...", options=options)

        async def select_callback(interaction2: discord.Interaction):
            await interaction2.response.defer()
            track = results[int(select.values[0])]
            await self._enqueue_track(interaction2, track)
            await interaction2.followup.send(embed=discord.Embed(description=f'🎶 Selected: {track}', color=discord.Color.green()), ephemeral=True)

        select.callback = select_callback
        view = View(timeout=60)
        view.add_item(select)
        await interaction.followup.send("🔎 Search results:", view=view)

    async def _enqueue_track(self, interaction: discord.Interaction, track: Track):
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
        state.songs.put_nowait(track)

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
        # Track carries requester_id, never a Member -- a Member is not
        # serializable, which is the whole reason the queue is plain data.  The
        # old `state.current.requester` raised AttributeError, so every /skip by
        # the requester failed instead of skipping.
        if voter.id == state.current.requester_id:
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
        if state.current.duration_seconds and pos > state.current.duration_seconds:
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
        if state.current.duration_seconds:
            new_pos = min(new_pos, state.current.duration_seconds)
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
                            value=f"[{state.current.title}]({state.current.url})\n"
                                  f"Requested by {state.current.requester_mention} • `{state.current.duration}`",
                            inline=False)
            embed.add_field(name='Loop Mode', value=state.loop.capitalize(), inline=True)
            embed.add_field(name='Volume', value=f"{int(state.volume * 100)}%", inline=True)

        upcoming = list(state.songs)[start:end]
        if upcoming:
            queue_text = '\n'.join(
                f'`{i + 1}.` [{song.title}]({song.url}) • {song.duration} • {song.requester_mention}'
                for i, song in enumerate(upcoming, start=start)
            )
            embed.add_field(name=f'Up Next ({total} total)', value=queue_text, inline=False)
        elif total == 0:
            embed.add_field(name='Up Next', value='No more songs in queue.', inline=False)

        total_duration = state.current.duration_seconds if state.is_playing else 0
        for song in state.songs:
            total_duration += song.duration_seconds or 0
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
            removed.append(song.title)
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

    @app_commands.command(name='autoplay', description='Keeps the music going with a YouTube Mix when the queue runs out.')
    async def autoplay(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        state._autoplay_enabled = not state._autoplay_enabled
        status = "enabled" if state._autoplay_enabled else "disabled"
        await interaction.response.send_message(
            embed=discord.Embed(description=f'📻 Autoplay {status}.', color=discord.Color.green()))
        state.changed()

    # ---------------- Playlists ----------------

    def _queue_payload(self, state: VoiceState) -> list[dict]:
        """The current track followed by the queue, as storable rows.

        The current track is included because "save this" means what is playing
        plus what is coming, not just the part nobody has heard yet.
        """
        tracks = ([state.current] if state.current else []) + list(state.songs)
        return [
            {'title': track.title, 'url': track.url,
             'duration_s': track.duration_seconds, 'source': track.source}
            for track in tracks
        ]

    @app_commands.command(name='save', description='Saves the current queue as a playlist.')
    @app_commands.describe(name="A name to save it under", public="Let anyone in this server load it")
    async def save(self, interaction: discord.Interaction, name: str, public: bool = False):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        state = self.peek_voice_state(interaction.guild.id)
        if state is None or not (state.current or len(state.songs)):
            await interaction.response.send_message("There is nothing to save.", ephemeral=True)
            return

        await interaction.response.defer()
        try:
            saved = await asyncio.to_thread(
                save_playlist, str(interaction.user.id), name, self._queue_payload(state),
                guild_id=str(interaction.guild.id), is_public=public)
        except PlaylistError as exc:
            await interaction.followup.send(embed=discord.Embed(description=f'❌ {exc}', color=discord.Color.red()))
            return
        except Exception as exc:
            log.exception("Could not save a playlist")
            await interaction.followup.send(
                embed=discord.Embed(description=f'❌ Could not save the playlist: {exc}', color=discord.Color.red()))
            return

        await interaction.followup.send(embed=discord.Embed(
            description=f'💾 Saved **{saved["track_count"]}** track(s) as **{saved["name"]}**.',
            color=discord.Color.green()))

    @app_commands.command(name='load', description='Loads a saved playlist into the queue.')
    @app_commands.describe(name="The playlist to load")
    async def load(self, interaction: discord.Interaction, name: str):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You need to be in a voice channel.", ephemeral=True)
            return

        await interaction.response.defer()
        try:
            playlist = await asyncio.to_thread(
                find_playlist, str(interaction.user.id), name, guild_id=str(interaction.guild.id))
        except Exception as exc:
            log.exception("Could not load a playlist")
            await interaction.followup.send(
                embed=discord.Embed(description=f'❌ Could not read your playlists: {exc}', color=discord.Color.red()))
            return
        if playlist is None:
            await interaction.followup.send(
                embed=discord.Embed(description=f'❌ No playlist called **{name}**.', color=discord.Color.red()))
            return

        added = await self._enqueue_playlist(interaction.guild, interaction.user,
                                             interaction.user.voice.channel, playlist)
        await interaction.followup.send(embed=discord.Embed(
            description=f'📀 Queued **{added}** track(s) from **{playlist["name"]}**.',
            color=discord.Color.green()))

    async def _enqueue_playlist(self, guild, member, destination, playlist: dict) -> int:
        """Connect if needed and append a stored playlist to the queue.

        Nothing is resolved here.  Stored rows become Tracks as they are -- some
        with only a title -- and ``YTDLSource.from_track`` does the lookup when
        each one actually plays.  Resolving 200 rows up front would take minutes
        and throw most of the work away the moment somebody ran /skip.
        """
        state = self.ensure_voice_state(guild.id)
        async with self._get_voice_lock(guild.id):
            if not state.voice or not state.voice.is_connected():
                state.voice = await destination.connect(self_deaf=True)
        state.start_player()

        for row in playlist['tracks']:
            state.songs.put_nowait(Track(
                title=row['title'], url=row['url'], duration_seconds=row['duration_s'] or 0,
                requester_id=member.id, requester_mention=member.mention, source=row['source']))
        state.changed()
        return len(playlist['tracks'])

    @app_commands.command(name='playlists', description='Lists your saved playlists.')
    async def playlists(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id) if interaction.guild else None
        try:
            saved = await asyncio.to_thread(list_playlists, str(interaction.user.id), guild_id=guild_id)
        except Exception as exc:
            log.exception("Could not list playlists")
            await interaction.followup.send(f'❌ Could not read your playlists: {exc}', ephemeral=True)
            return

        if not saved:
            await interaction.followup.send(
                "You have no saved playlists yet. Queue something up and run `/save`.", ephemeral=True)
            return

        embed = discord.Embed(title='📀 Your playlists', color=discord.Color.blurple())
        for playlist in saved[:25]:
            owned = playlist['owner_id'] == str(interaction.user.id)
            label = playlist['name'] if owned else f"{playlist['name']} (shared)"
            embed.add_field(
                name=label,
                value=f"{playlist['track_count']} track(s) • {_format_timestamp(playlist['duration_s'])}",
                inline=False)
        if len(saved) > 25:
            embed.set_footer(text=f'Showing 25 of {len(saved)}.')
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name='playlist-delete', description='Deletes one of your saved playlists.')
    @app_commands.describe(name="The playlist to delete")
    async def playlist_delete(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        try:
            playlist = await asyncio.to_thread(find_playlist, str(interaction.user.id), name)
        except Exception as exc:
            log.exception("Could not delete a playlist")
            await interaction.followup.send(f'❌ Could not read your playlists: {exc}', ephemeral=True)
            return
        # find_playlist without a guild only ever returns your own, so ownership
        # is already established -- but assert it rather than rely on that.
        if playlist is None or playlist['owner_id'] != str(interaction.user.id):
            await interaction.followup.send(f'❌ You have no playlist called **{name}**.', ephemeral=True)
            return
        await asyncio.to_thread(delete_playlist, playlist['id'])
        await interaction.followup.send(f'🗑️ Deleted **{playlist["name"]}**.', ephemeral=True)

    async def _bridge_playlist_load(self, guild, actor_id, args):
        member = self._authorize(guild, actor_id)
        try:
            playlist_id = int(args.get('playlist_id'))
        except (TypeError, ValueError):
            raise VoiceError("That is not a playlist.") from None
        playlist = await asyncio.to_thread(get_playlist, playlist_id)
        if playlist is None:
            raise VoiceError("That playlist no longer exists.")
        # A private playlist is loadable only by its owner, wherever it was saved.
        if playlist['owner_id'] != str(member.id) and not playlist['is_public']:
            raise VoiceError("That playlist is not yours.")

        state = self.peek_voice_state(guild.id)
        destination = state.voice.channel if state and state.voice and state.voice.is_connected() else None
        if destination is None:
            destination = member.voice.channel if member.voice else None
        if destination is None:
            raise VoiceError("Join a voice channel first.")

        added = await self._enqueue_playlist(guild, member, destination, playlist)
        return {'added': added, 'name': playlist['name']}

    async def _bridge_autoplay(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        state._autoplay_enabled = bool(args.get('enabled'))
        return {'autoplay': state._autoplay_enabled}

    # ---------------- Spotify Helpers ----------------

    # The two former staticmethods now live at module level (see the predicates at
    # the top of this file).  These aliases keep the ~4 existing `self.` call sites
    # working without touching them.
    _resolve_spotify_short_link = staticmethod(_resolve_spotify_short_link)
    _parse_spotify_id = staticmethod(_parse_spotify_id)

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
            log.exception("Spotify lookup failed")
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
            title = state.current.title
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
            log.exception("Could not fetch lyrics")
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
