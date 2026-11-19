"""Music system (Groovy-inspired): YouTube/Spotify playback, queue, audio effects,
seek controls, and lyrics.

Ported 1:1 from the original bot.py (lines 899-2211). Time helpers come from
``utils.time_utils`` and the FFmpeg path from ``core.ffmpeg``.
"""

import math
import random
import asyncio
import copy
import dataclasses
import functools
import itertools
import logging
import shlex
import threading
import traceback
import time
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks
from discord.ui import Button, View, Select
import yt_dlp

from zephyr.config import REDIS_URL, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
from zephyr.core.ffmpeg import FFMPEG_PATH
from zephyr.db.guild_settings import read_dj_roles, read_music_settings
from zephyr.db.playlists import (
    PlaylistError,
    clean_source,
    delete_playlist,
    find_playlist,
    get_playlist,
    list_playlists,
    save_playlist,
)
from zephyr.services import bridge
from zephyr.services.spotify import (
    build_client as build_spotify_client,
    fetch_playlist_metadata,
    is_spotify_url,
    parse_spotify_id,
    resolve_short_link,
)
from zephyr.utils import net
from zephyr.utils.net import is_internal_host
from zephyr.utils.time_utils import _parse_user_time, _format_timestamp


LOGGER = logging.getLogger(__name__)


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


def _drop_auto_mix(search: str) -> str:
    """A watch link that merely came *from* a YouTube Mix plays just that video.

    Clicking a song inside a Mix gives ``watch?v=X&list=RDX...``.  With
    ``noplaylist`` off, yt-dlp expanded that into the whole auto-generated Mix,
    so pasting one song queued 200.  Real playlists (``list=PL...``) and
    ``/playlist`` links are left alone.
    """
    if not _is_youtube_url(search):
        return search
    parts = urlsplit(_sanitize_search(search))
    query = parse_qsl(parts.query, keep_blank_values=True)
    names = {key for key, _ in query}
    mix = any(key == 'list' and value.upper().startswith('RD') for key, value in query)
    if 'v' not in names or not mix:
        return search
    kept = [(key, value) for key, value in query if key not in {'list', 'index', 'start_radio', 'pp'}]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def _is_internal_url(search: str) -> bool:
    """Whether a URL points at this host or a private network.

    yt-dlp's generic extractor and FFmpeg will fetch any http(s) URL, and the
    dashboard lets anyone with a session submit one -- so without this the bot
    could be pointed at services only reachable from its own network.  Only
    literal addresses and local names are refused; resolving every hostname
    would cost a DNS round trip per query for little extra protection.
    """
    try:
        host = (urlsplit(_sanitize_search(search)).hostname or '').strip('[]').lower()
    except ValueError:
        return True
    # Shared with the website, and it also understands 127.1, 2130706433 and
    # 0x7f000001 -- forms getaddrinfo resolves to localhost but ipaddress rejects.
    return is_internal_host(host)


INTERNAL_LINK_MESSAGE = "Links to private or local addresses can't be played."


def _refuse_internal(search: str) -> None:
    if _is_url(search) and _is_internal_url(search):
        raise InternalLinkError(INTERNAL_LINK_MESSAGE)


def _refuse_resolving_internal(url: str | None) -> None:
    """The DNS half of the check.  Blocking: run it on a worker thread.

    ``_refuse_internal`` only reads the URL, so a public name pointing inside
    (``127.0.0.1.nip.io``) passed it, and so did a public page whose extracted
    media URL was internal -- FFmpeg fetched both.
    """
    if url and _is_url(url) and net.url_resolves_internal(_sanitize_search(url)):
        raise InternalLinkError(INTERNAL_LINK_MESSAGE)


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

# Recent YouTube responses can deliberately delay a signed media URL.  yt-dlp's
# downloader honours ``available_at`` before fetching it; our FFmpeg handoff
# must do the same, while a cap prevents a malformed extractor response from
# stalling a guild indefinitely.
STREAM_AVAILABLE_MAX_WAIT_SECONDS = 30

# Googlevideo routinely drops long-lived HTTP connections mid-track.  Without
# these, FFmpeg treats the drop as a clean EOF and the song stops partway
# through.  ``-reconnect_at_eof`` is deliberately absent: it can loop at the
# genuine end of a track.
# A flat (process=False) extraction lists a search or a playlist without
# resolving any streams.  It is usually quick, but had no timeout at all, so a
# stalled request held /play "thinking" forever.
FLAT_EXTRACT_TIMEOUT_SECONDS = 60
# How long an idle player (empty queue, not 24/7) stays connected.
IDLE_DISCONNECT_SECONDS = 180
# The idle wait is sliced so /247 and /autoplay toggled while idle take effect.
IDLE_POLL_SECONDS = 5
# Upper bound on entries read from one flat extraction (playlists, searches, Mixes).
MAX_FLAT_ENTRIES = 200
# yt-dlp gets its own threads.  A timed-out extraction keeps running in its
# thread (wait_for cannot stop it), and on the shared default executor a few
# stuck lookups starved database, Redis and TTS work queued behind them.
_YTDL_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix='ytdl')

FFMPEG_RECONNECT_OPTIONS = '-reconnect 1 -reconnect_streamed 1 -reconnect_on_network_error 1 -reconnect_delay_max 5'

# Speed effects, as the factor each one plays the source at.  They are applied
# as one resample so they compose, and the product is also what the position
# clock runs at (see VoiceState.elapsed).
SPEED_EFFECTS = {
    '_nightcore_enabled': 1.25,
    '_vaporwave_enabled': 0.8,
    '_slowed_enabled': 0.9,
    '_slownrev_enabled': 0.9,
}
EFFECT_SAMPLE_RATE = 48000

# Reading a Spotify playlist is a handful of API pages; this is generous.
SPOTIFY_TIMEOUT_SECONDS = 60

# Lower-cased fragments of FFmpeg stderr that mean "this source never played",
# as opposed to a track that reached its end.
FFMPEG_FAILURE_MARKERS = (
    'http error', 'error opening input', 'invalid data', 'server returned', 'connection refused',
    'no such filter', 'filter not found', 'error initializing filter', 'error opening output',
)
# A track longer than this that ends within EARLY_END_SECONDS of starting, with
# no skip or stop requested, failed rather than finished.
MIN_TRACK_SECONDS_FOR_EARLY_END = 10
EARLY_END_SECONDS = 2.0
REVERB_FILTER = 'aecho=0.8:0.88:60:0.4'

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


# Rows listed in the /remove reply; the rest are counted.
REMOVE_LIST_LIMIT = 10


# What a flat YouTube playlist extraction calls a video that is gone.  They
# have no stream, and searching YouTube for the title played a random video
# (and "healed" a saved playlist to it).
UNAVAILABLE_TITLES = frozenset({'[deleted video]', '[private video]', '[unavailable video]'})


def _is_unavailable_title(title: str | None) -> bool:
    return (title or '').strip().lower() in UNAVAILABLE_TITLES


def _short(text: str, limit: int | None) -> str:
    text = text or ''
    if limit and len(text) > limit:
        return text[:limit - 1].rstrip() + '…'
    return text


def _link(track: 'Track', max_title: int | None = None) -> str:
    """Markdown link to a track, or its bare title before it has a URL.

    Spotify and imported tracks are resolved only when they play, and
    ``[title](None)`` rendered as a broken link in /queue.  Square brackets in
    a title closed the link early, so they are swapped for parentheses.
    """
    title = _short(track.title, max_title).replace('[', '(').replace(']', ')')
    return f'[{title}]({track.url})' if track.url else title


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


def _coerce_bool(value, name: str) -> bool:
    """Read one on/off flag out of a bridge command's args.

    ``bool("false")`` is True, so a browser sending the string turned an effect
    on (or reset every effect) instead of off.  Only real booleans, 0/1 and the
    obvious words are accepted.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {'true', 'on', 'yes', '1'}:
        return True
    if isinstance(value, str) and value.strip().lower() in {'false', 'off', 'no', '0'}:
        return False
    raise VoiceError(f"`{name}` must be true or false.")


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
    # Validate first, assign after: a bad pitch used to be rejected only after
    # the toggles were already set, leaving effects half-applied -- shown as on
    # in the snapshot and used by the next track, but never restarted into the
    # current one.
    pitch = boost = None
    if 'pitch' in args:
        pitch = _coerce_float(args['pitch'], 'pitch')
        if not 0.5 <= pitch <= 2.0:
            raise VoiceError("Pitch must be between 0.5 and 2.0.")
    if 'bass_boost' in args and args['bass_boost'] is not None:
        boost = int(_coerce_float(args['bass_boost'], 'bass_boost'))
        if not -20 <= boost <= 20:
            raise VoiceError("Bass boost must be between -20 and 20 dB.")

    reset = _coerce_bool(args.get('reset', False), 'reset')
    toggles = {name: _coerce_bool(args[name], name) for name in _TOGGLE_EFFECTS if name in args}

    if reset:
        for attribute, _ in _TOGGLE_EFFECTS.values():
            setattr(state, attribute, False)
        state._pitch = 1.0
        state._bass_boost = None

    for name, (attribute, excludes) in _TOGGLE_EFFECTS.items():
        if name not in toggles:
            continue
        enabled = toggles[name]
        setattr(state, attribute, enabled)
        if enabled:
            for other in excludes:
                setattr(state, other, False)

    if pitch is not None:
        state._pitch = pitch
    if 'bass_boost' in args:
        state._bass_boost = boost


class YTDLError(Exception):
    pass


def _refuse_past_deadline() -> None:
    """Refuse to change the queue once a dashboard caller has stopped waiting.

    The dashboard has already shown "did not respond"; queueing now would
    surprise the user, and a retry would queue it twice.  No deadline (a slash
    command) never refuses.
    """
    deadline = bridge.command_deadline.get()
    if deadline is not None and time.time() > deadline:
        raise VoiceError("That took too long, so nothing was queued. Try again.")


class InternalLinkError(YTDLError):
    """A link to this host or a private network.  Never retried by title."""


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
    # Set after one failed stream so the next failure drops the track instead
    # of retrying forever.  On the track itself: a set of id(track) leaked
    # entries for removed tracks, and a recycled id could make a brand-new
    # track look as if it had already used its retry.
    stream_retried: bool = dataclasses.field(default=False, compare=False, repr=False)

    @property
    def duration(self) -> str:
        return _format_duration(self.duration_seconds)

    def __str__(self):
        if not self.uploader or self.uploader == 'Unknown':
            # Unresolved Spotify/imported tracks: the title already names the artist.
            return f'**{self.title}**'
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
        uploader = self.uploader or 'Unknown'
        # Flat, Spotify and imported tracks have no uploader URL until they play;
        # a link to "None" rendered as broken markdown.
        embed.add_field(name='Uploader',
                        value=f'[{uploader}]({self.uploader_url})' if self.uploader_url else uploader,
                        inline=True)
        if self.url:
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


class _FFmpegDiagnostics:
    """Thread-safe, bounded stderr sink accepted by discord.py's FFmpeg source."""

    def __init__(self, limit: int = 8_192):
        self._limit = limit
        self._data = bytearray()
        self._lock = threading.Lock()

    def write(self, data: bytes) -> int:
        if isinstance(data, str):
            data = data.encode("utf-8", "replace")
        with self._lock:
            self._data.extend(data)
            if len(self._data) > self._limit:
                del self._data[:-self._limit]
        return len(data)

    def flush(self) -> None:
        pass

    def tail(self) -> str:
        with self._lock:
            return bytes(self._data).decode("utf-8", "replace").strip()


class YTDLSource(discord.PCMVolumeTransformer):
    YTDL_OPTIONS = {
        'format': 'bestaudio/best',
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': False,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        # Node is explicitly enabled: yt-dlp intentionally enables only Deno by
        # default, even when Node is installed. requirements.txt installs the
        # matching yt-dlp-ejs component via the ``default`` extra.
        'js_runtimes': {'node': {}},
    }

    DEFAULT_FFMPEG_OPTIONS = {
        'before_options': FFMPEG_RECONNECT_OPTIONS,
        'options': '-vn',
    }

    # One YoutubeDL per worker thread, not one shared by all four: an instance
    # carries per-call state (playlist recursion, cookies, extractor caches) and
    # is not thread-safe, so concurrent lookups could corrupt each other.
    # Tests replace ``ytdl`` with a double, which is then used as is.
    ytdl = None
    _ytdl_local = threading.local()

    @classmethod
    def _ytdl(cls):
        if cls.ytdl is not None:
            return cls.ytdl
        instance = getattr(cls._ytdl_local, 'instance', None)
        if instance is None:
            instance = cls._ytdl_local.instance = yt_dlp.YoutubeDL(cls.YTDL_OPTIONS)
        return instance

    def __init__(self, source: discord.FFmpegPCMAudio, *, data: dict, diagnostics: _FFmpegDiagnostics,
                 volume: float = 0.5):
        super().__init__(source, volume)
        self.data = data
        self.stream_url = data.get('url')
        self._diagnostics = diagnostics

    @property
    def stderr_tail(self) -> str:
        return self._diagnostics.tail()

    @staticmethod
    def _ffmpeg_headers(headers: dict | None) -> str:
        """Return a safely quoted FFmpeg ``-headers`` argument for yt-dlp media URLs."""
        lines = []
        for name, value in (headers or {}).items():
            clean_name = str(name).replace("\r", "").replace("\n", "").strip()
            clean_value = str(value).replace("\r", "").replace("\n", "").strip()
            if clean_name and clean_value:
                lines.append(f"{clean_name}: {clean_value}")
        if not lines:
            return ""
        header_block = "\r\n".join(lines) + "\r\n"
        return f"-headers {shlex.quote(header_block)}"

    @classmethod
    def _make_ffmpeg_source(cls, url: str, *, data: dict, options: dict, volume: float) -> 'YTDLSource':
        diagnostics = _FFmpegDiagnostics()
        return cls(
            discord.FFmpegPCMAudio(url, executable=FFMPEG_PATH, stderr=diagnostics, **options),
            data=data,
            diagnostics=diagnostics,
            volume=volume,
        )

    @staticmethod
    async def _wait_for_stream_availability(info: dict) -> float:
        """Wait for yt-dlp's signed URL readiness time, returning the delay used."""
        try:
            available_at = float(info.get('available_at') or 0)
        except (TypeError, ValueError):
            return 0.0
        delay = available_at - time.time()
        if delay <= 0:
            return 0.0
        delay = min(delay, STREAM_AVAILABLE_MAX_WAIT_SECONDS)
        LOGGER.info("Waiting %.1fs for the YouTube stream to become available.", delay)
        await asyncio.sleep(delay)
        return delay

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
    async def _extract(cls, query: str, *, loop, process: bool = True, timeout_s: int = 15,
                       max_entries: int = MAX_FLAT_ENTRIES):
        _refuse_internal(query)
        partial = functools.partial(cls._extract_sync, query, process=process, max_entries=max_entries)
        return await asyncio.wait_for(loop.run_in_executor(_YTDL_EXECUTOR, partial),
                                      timeout=timeout_s if process else FLAT_EXTRACT_TIMEOUT_SECONDS)

    @classmethod
    def _extract_sync(cls, query: str, *, process: bool, max_entries: int):
        """Run yt-dlp and materialise its entries, all on the worker thread.

        With ``process=False`` yt-dlp returns ``entries`` as a lazy generator that
        downloads search and playlist pages *as it is iterated*.  Iterating it
        later, on the event loop, blocked every guild and the gateway heartbeat
        for the whole lookup, outside any timeout -- and a 2,000-video playlist
        was read to the end before being cut to 200.
        """
        _refuse_resolving_internal(query)
        data = cls._ytdl().extract_info(query, download=False, process=process)
        if isinstance(data, dict) and process:
            # What FFmpeg will actually open, which a public page can point anywhere.
            _refuse_internal(data.get('url') or '')
            _refuse_resolving_internal(data.get('url'))
        if isinstance(data, dict) and data.get('entries') is not None:
            entries = data['entries']
            if not isinstance(entries, list):
                entries = list(itertools.islice(entries, max_entries or None))
            elif max_entries:
                entries = entries[:max_entries]
            data = {**data, 'entries': entries}
        return data

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
        _refuse_internal(search)

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
            data = await cls._extract(query, loop=loop, process=False,
                                      max_entries=max_entries or MAX_FLAT_ENTRIES)
            if data is None:
                raise YTDLError(f'Could not find anything that matches `{_short(search, 200)}`')

            entries = list(data.get('entries') or [])

            # Plain-text search: take only the top hit rather than enqueueing ten.
            if not as_url:
                if not entries:
                    raise YTDLError(f'Could not find anything that matches `{_short(search, 200)}`')
                tracks = cls._build_tracks_from_entries(
                    entries[:1], requester_id=requester_id, requester_mention=requester_mention, source='search')
                if not tracks:
                    raise YTDLError(f'Could not find anything that matches `{_short(search, 200)}`')
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
            traceback.print_exc()
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
            if not url or _is_unavailable_title(entry.get('title')):
                skipped += 1
                continue
            tracks.append(Track.from_info({**entry, 'webpage_url': url},
                                          requester_id=requester_id,
                                          requester_mention=requester_mention,
                                          source=source))
        if skipped:
            print(f"[Playlist] Added {len(tracks)} tracks, skipped {skipped} unavailable or with no usable URL.")
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
            data = await cls._extract(f"ytsearch{max_results}:{search}", loop=loop, process=False,
                                      max_entries=max_results)
            if data is None or not data.get('entries'):
                return []
            return cls._build_tracks_from_entries(list(data['entries']),
                                                  requester_id=requester_id,
                                                  requester_mention=requester_mention,
                                                  source='search')
        except asyncio.TimeoutError:
            raise YTDLError(f'Searching for `{search}` timed out.') from None
        except Exception as e:
            traceback.print_exc()
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

        # Saved playlists are editable from the web, so every stored link is
        # checked again here -- not only direct files: yt-dlp's generic
        # extractor fetches any URL it is handed.
        _refuse_internal(track.url or '')
        if track.source == 'file' and track.url:
            # Direct media: hand the URL straight to FFmpeg, no extraction --
            # so the DNS check yt-dlp lookups get happens here instead.
            await loop.run_in_executor(_YTDL_EXECUTOR, _refuse_resolving_internal, track.url)
            return cls._make_ffmpeg_source(track.url, data={'url': track.url, 'title': track.title},
                                           options=options, volume=volume)

        info = None
        if track.url:
            try:
                info = await cls._extract(track.url, loop=loop)
            except (asyncio.TimeoutError, InternalLinkError):
                raise
            except Exception as exc:
                if not track.title or track.title == track.url or _is_unavailable_title(track.title):
                    raise YTDLError(f'Could not fetch `{track.url}`: {exc}')
                print(f"[Re-resolve] {track.url} failed ({exc}); searching for '{track.title}'.")
        elif not track.title or _is_unavailable_title(track.title):
            raise YTDLError('That track has neither a link nor a title.')

        if not info or not info.get('url'):
            info = await cls._resolve_by_title(track, loop=loop)

        if not info or 'url' not in info:
            raise YTDLError(f'Could not fetch `{track.title or track.url}`')
        await cls._wait_for_stream_availability(info)
        # A flat entry's title/duration are placeholders until now.
        track.absorb(info)
        # FFmpeg already sends ``Range: bytes=<offset>-`` on every request, and
        # updates the offset when it reconnects.  A user-supplied Range header
        # suppresses that, so every reconnect would restart the track from 0.
        headers_data = {name: value for name, value in (info.get('http_headers') or {}).items()
                        if str(name).strip().lower() != 'range'}
        headers = cls._ffmpeg_headers(headers_data)
        if headers:
            options['before_options'] = f"{options.get('before_options', '')} {headers}".strip()
        return cls._make_ffmpeg_source(info['url'], data=info, options=options, volume=volume)

    @classmethod
    async def _resolve_by_title(cls, track: 'Track', *, loop) -> dict | None:
        """Find a playable video for a track that has only a title.

        The track is mutated with what was found, so a saved playlist heals
        itself: the next play uses the repaired URL directly instead of searching
        again.  ``repaired_from`` is only set when there was something to repair,
        which keeps the "playing a re-resolved match" footer off imports that
        never had a URL in the first place.
        """
        found = await cls._extract(f"ytsearch1:{track.title}", loop=loop, process=False, max_entries=1)
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
        # Run the action itself, but publish the dashboard snapshot only after
        # Discord has been answered: a slow Redis write used to push the reply
        # past Discord's three seconds, so the action happened and the user was
        # still told "This interaction failed".
        raw = getattr(handler, '__wrapped__', None)
        try:
            await (raw or handler)(interaction.guild, interaction.user.id, args or {})
        except (VoiceError, YTDLError) as exc:
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True)
            return
        except Exception as exc:
            traceback.print_exc()
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True)
            return
        # The refresh loop redraws the embed within a few seconds; acknowledging
        # silently keeps the channel free of one message per button press.
        await interaction.response.defer()
        if raw is not None and interaction.guild is not None:
            await self.cog.publish_state(interaction.guild.id)

    @discord.ui.button(emoji='⏯️', style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction: discord.Interaction, button: Button):
        state = self.cog.peek_voice_state(self.guild_id)
        paused = bool(state and state.voice and state.voice.is_paused())
        await self._run(interaction, 'player.resume' if paused else 'player.pause')

    @discord.ui.button(emoji='⏭️', style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: Button):
        # The same rule as /skip: instant for whoever queued the song, a DJ or
        # a server manager; a vote for everyone else.  The button used to skip
        # outright for any listener, which made the documented vote pointless.
        state = self.cog.peek_voice_state(self.guild_id)
        member = interaction.user
        if state is None or state.current is None or self.cog._may_force_skip(state, member):
            await self._run(interaction, 'player.skip')
            return
        outcome, text = state.vote_skip(member)
        await interaction.response.send_message(text, ephemeral=True)
        if outcome == 'skipped' and interaction.guild is not None:
            await self.cog.publish_state(interaction.guild.id)

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
        # The track that played last, kept after it ends so loop=track can replay
        # it and autoplay can seed a radio from it.  ``current`` itself is cleared
        # the moment a track ends: a finished track left in ``current`` made an
        # idle player look busy, and a seek or effect toggle then "restarted" it.
        self._last_track: Track | None = None
        self.source: YTDLSource | None = None  # live audio; only while playing
        self.voice: discord.VoiceClient = None
        self.next = asyncio.Event()
        self.songs = SongQueue()
        self.exists = True
        # Bumped by clear_queue(); an autoplay refill started before it is dropped.
        self._refill_generation = 0

        self._loop_mode = 'off'  # 'off', 'track', 'queue'
        self._volume = 0.5
        self.skip_votes = set()
        self.manual_stop = False
        # Sources restart_current replaced.  Their after-callbacks still fire
        # (on the player thread, at a time we do not control) and must be
        # ignored whenever they arrive -- a boolean consumed by "the next
        # callback" raced with fast restarts and skipped the track instead.
        self._retired_sources = set()
        self._restart_lock = asyncio.Lock()
        # One-shot intents for the next loop iteration.  A skip must move on
        # even when loop=track (which otherwise replays self.current), and a
        # stop must not be undone by loop=track or by autoplay refilling the
        # queue it just cleared.
        self._skip_requested = False
        self._halt_requested = False
        self._stopping = False
        # The AudioPlayer after-callback runs in a worker thread.  It records a
        # failed FFmpeg source here before waking audio_player_task on the event
        # loop; the task owns the retry and all Discord API calls.
        self._playback_failure = None
        # A /seek that arrived while the track was still loading; applied by
        # audio_player_task once the load finishes.
        self._pending_seek = None

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
        # The speed the playing source runs at, fixed when it started: the
        # effect flags may already have changed by the time elapsed is read
        # (restart_current reads it *after* a toggle, to find where to resume).
        self._current_rate = 1.0
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

    def clear_queue(self) -> None:
        """Empty the queue, including an autoplay refill still being looked up."""
        self._refill_generation += 1
        self.songs.clear()

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
        previous, self._loop_mode = self._loop_mode, value
        # Queue loop works by re-queueing each track as it starts.  Switching
        # mid-track used to leave the playing song out of the rotation (on), or
        # leave its copy behind to play once more at the end (off).
        current = self.current
        if current is None or previous == value:
            return
        if value == 'queue':
            self.songs.put_nowait(current)
        elif previous == 'queue':
            for index in range(len(self.songs) - 1, -1, -1):
                if self.songs[index] is current:
                    self.songs.remove(index)
                    break

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
        """Position in the *source*, in seconds.

        Wall-clock time scaled by the playback rate: under nightcore a minute of
        listening covers 75 seconds of the track, and treating them as equal
        made the progress bar, /forward and every effect restart drift.
        """
        if self._current_start_time is None:
            return self._current_position
        return self._current_position + (time.time() - self._current_start_time) * self._current_rate

    def playback_rate(self) -> float:
        """How fast the effect chain plays the source: 1.0 is normal speed."""
        rate = 1.0
        for attribute, factor in SPEED_EFFECTS.items():
            if getattr(self, attribute):
                rate *= factor
        return rate

    def get_ffmpeg_options(self, seek_position: float = None):
        options = {
            'before_options': FFMPEG_RECONNECT_OPTIONS,
            'options': '-vn'
        }
        if seek_position is not None and seek_position > 0:
            options['before_options'] += f' -ss {seek_position}'
        filters = []
        rate = self.playback_rate()
        if rate != 1.0:
            # Resample to a known rate first: asetrate reinterprets whatever rate
            # the input has, so a fixed target was a no-op on YouTube's 48 kHz
            # opus and a different effect on 44.1 kHz m4a.  Scaling a known
            # rate changes speed and pitch together, which is the effect.
            filters.append(f"aresample={EFFECT_SAMPLE_RATE},"
                           f"asetrate={round(EFFECT_SAMPLE_RATE * rate)},"
                           f"aresample={EFFECT_SAMPLE_RATE}")
        if self._16d_enabled:
            filters.append("apulsator=hz=0.08")
        if self._reverb_enabled or self._slownrev_enabled:
            # FFmpeg has no "reverb" filter (that is SoX syntax); asking for one
            # made FFmpeg exit before producing a sample, on every track.
            filters.append(REVERB_FILTER)
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
            # How many seconds of track one second of listening covers, so the
            # dashboard's local progress tick matches a sped-up or slowed track.
            'rate': self._current_rate,
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
            print(f"[Music] Could not post to channel {self.np_channel_id}: {exc}")
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
            print(f"[Music] Could not refresh the now-playing message: {exc}")

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
            print(f"[Music] Could not retire the now-playing message: {exc}")

    async def _extend_with_radio(self) -> int:
        """Top the queue up from YouTube's Mix for the last track played.

        A Mix (``list=RD<video id>``) is YouTube's own related-tracks radio, so
        this needs no recommendation logic of its own -- one flat extraction
        yields dozens of entries.  Anything played recently is filtered out,
        because a Mix always leads with its seed video and autoplay would
        otherwise put the same song on repeat.
        """
        seed_track = self.current or self._last_track
        seed = _video_id(seed_track.url) if seed_track else None
        if not seed:
            return 0
        generation = self._refill_generation
        try:
            tracks = await YTDLSource.resolve_tracks(
                f"https://www.youtube.com/watch?v={seed}&list=RD{seed}",
                requester_id=self.bot.user.id if self.bot.user else 0,
                requester_mention='Autoplay',
                loop=self.bot.loop,
                max_entries=AUTOPLAY_FETCH,
            )
        except Exception as exc:
            print(f"[Autoplay] Could not build a radio for {seed}: {exc}")
            return 0

        if generation != self._refill_generation:
            # /stop or /clear arrived during the (up to a minute) lookup: the
            # refill used to land afterwards and start five songs anyway.
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
            print(f"[Autoplay] Queued {added} track(s) from the radio for {seed}.")
        return added

    def _remember(self, track: 'Track') -> None:
        key = _video_id(track.url) or track.url
        if key:
            self._recent_ids.append(key)

    def _queue_track_for_retry(self, track: 'Track') -> None:
        """Put one failed track at the front without duplicating queue-loop items."""
        for index, queued in enumerate(self.songs):
            if queued is track:
                self.songs.move(index, 0)
                return
        self.songs.add_to_front(track)

    def _remove_queued_track(self, track: 'Track') -> None:
        """Drop the queue-loop copy after its one permitted stream retry fails."""
        for index, queued in enumerate(self.songs):
            if queued is track:
                self.songs.remove(index)
                return

    async def _wait_for_foreign_audio(self, timeout: float = 15.0) -> None:
        """Wait while the voice client is playing audio that is not ours (a TTS clip).

        Only a real ``True`` counts as busy, so test doubles and a voice client
        that cannot answer never stall the player.
        """
        voice = self.voice
        is_playing = getattr(voice, 'is_playing', None)
        if not callable(is_playing):
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                busy = is_playing()
            except Exception:
                return
            if busy is not True:
                return
            await asyncio.sleep(0.25)
        # Still talking after the timeout: music wins rather than being dropped.
        try:
            voice.stop()
        except Exception:
            pass

    def _track_ended(self) -> None:
        """Forget the source and the current track once playback has stopped."""
        # Unconditional: a failed stream clears ``current`` so that loop=track
        # moves on to the retry at the front of the queue instead of replaying.
        self._last_track = self.current
        self.current = None
        self.source = None
        self._current_start_time = None
        self._current_position = 0.0

    async def _wait_for_track(self, *, allow_radio: bool, radio_tried: bool) -> 'Track':
        """Wait for the next queued track; raise TimeoutError once idle too long.

        Waits in short slices rather than one long timeout, because the 24/7 and
        autoplay settings were read once before the wait: turning autoplay on
        while idle played nothing, and turning 24/7 on did not stop the
        disconnect three minutes later.
        """
        idle = 0.0
        while True:
            if not self._autoplay_enabled:
                radio_tried = False
            elif (allow_radio and not radio_tried and not len(self.songs)
                  and self.loop == 'off' and self._last_track is not None):
                radio_tried = True
                await self._extend_with_radio()
            try:
                return await asyncio.wait_for(self.songs.get(), timeout=IDLE_POLL_SECONDS)
            except asyncio.TimeoutError:
                if self._247_enabled:
                    idle = 0.0
                    continue
                idle += IDLE_POLL_SECONDS
                if idle >= IDLE_DISCONNECT_SECONDS:
                    raise

    async def audio_player_task(self):
        while True:
            self.skip_votes.clear()
            skipped, halted = self._skip_requested, self._halt_requested
            self._skip_requested = self._halt_requested = False
            if halted:
                self.current = None
                self._last_track = None

            # Refill before the idle timer starts, not after it fires: waiting
            # would disconnect the bot three minutes into an autoplay session.
            radio_tried = False
            if self._autoplay_enabled and not halted and not len(self.songs) and self.loop == 'off':
                radio_tried = True
                await self._extend_with_radio()

            if self.loop == 'track' and self._last_track and not skipped:
                track = self._last_track        # replay the same track
                self.current = track
            else:
                if not len(self.songs):
                    # Nothing left to play: leave the last now-playing message with
                    # its buttons disabled instead of controls that do nothing.
                    await self.retire_now_playing()
                # Only the wait for a *queued* track is the idle timeout.  The
                # extraction below raises asyncio.TimeoutError too (and on 3.11+
                # so does a socket timeout); catching both in one handler made a
                # slow YouTube lookup disconnect the bot and drop the queue.
                try:
                    track = await self._wait_for_track(allow_radio=not halted, radio_tried=radio_tried)
                except asyncio.TimeoutError:
                    if len(self.songs):
                        # A /play landed in the instant the wait timed out.
                        continue
                    # Marked dead *before* the disconnect is awaited: during it a
                    # /play used to find this state still alive, queue into it
                    # (or reconnect, only for _stop to drop the new client) and
                    # say "Enqueued" for a track that never played.
                    self.exists = False
                    # cancel_player=False because we are *inside* the player
                    # task: cancelling and awaiting ourselves only works by accident.
                    await self.stop(cancel_player=False)
                    return
                self.current = track
                if self.loop == 'queue' and track:
                    # Re-queue it so it plays again after everything else.
                    self.songs.put_nowait(track)

            # The options and the rate they imply are captured together, before
            # the lookup: an effect toggled during the 1-3 s extraction used to
            # set the progress clock to the new speed while the source played
            # at the old one.
            self._pending_seek = None
            start_at = 0.0
            options = self.get_ffmpeg_options()
            rate = self.playback_rate()
            try:
                source = await YTDLSource.from_track(track, ffmpeg_options=options,
                                                     volume=self._volume, loop=self.bot.loop)
                # /say may be speaking: voice.play() would raise "Already playing
                # audio", and the track went down the stream-failure path and
                # could be dropped.  Waited for here, *before* the checks below:
                # a skip, stop or seek sent during the clip used to be lost.
                await self._wait_for_foreign_audio()
                # A /seek or an effect change that arrived while the track was
                # loading had nothing to restart.  Apply it now, before play,
                # instead of reporting success and ignoring it.
                pending = self._pending_seek
                if pending is not None or self.get_ffmpeg_options() != options:
                    self._pending_seek = None
                    start_at = pending or 0.0
                    try:
                        source.cleanup()
                    except Exception:
                        pass
                    options = self.get_ffmpeg_options(seek_position=start_at)
                    rate = self.playback_rate()
                    source = await YTDLSource.from_track(track, ffmpeg_options=options,
                                                         volume=self._volume, loop=self.bot.loop)
            except Exception as e:
                reason = 'the lookup timed out' if isinstance(e, asyncio.TimeoutError) else str(e)
                await self._notify(embed=discord.Embed(description=f'❌ Failed to load **{track.title}**: {reason}',
                                                       color=discord.Color.red()))
                traceback.print_exc()
                # A track that cannot be loaded must not come round again: in
                # queue-loop mode its re-queued copy kept the queue non-empty
                # forever, re-extracting the dead video every few seconds.
                self._remove_queued_track(track)
                self.current = None
                # Nor in track-loop mode: _last_track still pointed at it, so
                # every pass replayed the dead track and posted another error.
                self._last_track = None
                continue

            if self._skip_requested or self._halt_requested or self.current is not track:
                # /skip, /stop or /jump arrived while the track was loading.  There
                # was no audio for voice.stop() to end, so honour the request here
                # instead of playing the track anyway.
                try:
                    source.cleanup()
                except Exception:
                    pass
                if self.current is track:
                    self._track_ended()
                continue

            try:
                # Cleared here rather than at the top of the loop, so a stale signal
                # from while the track was loading cannot end this one at once.
                self.next.clear()
                self._playback_failure = None
                self.source = source
                self._current_start_time = time.time()
                self._current_position = start_at
                self._current_rate = rate
                self.voice.play(source, after=lambda error: self.play_next_song(error, source=source, track=track))
                self._remember(track)
                self.changed()
                await self.announce_now_playing(track)
            except Exception as e:
                # The source owns a live ffmpeg process; if play() never took ownership
                # of it, nothing else will ever clean it up.
                try:
                    source.cleanup()
                except Exception:
                    pass
                await self._notify(content=f'An error occurred while playing: {e}')
                traceback.print_exc()
                self.play_next_song(e, source=source, track=track)
                if self.source is source:
                    self.source = None

            await self.next.wait()

            failure = self._playback_failure
            if failure and failure['track'] is track:
                self._playback_failure = None
                diagnostics = failure['diagnostics']
                LOGGER.error("FFmpeg failed while streaming %r: %s", track.title, diagnostics)
                if not track.stream_retried:
                    # yt-dlp URLs are short-lived.  One fresh extraction is cheap
                    # and recovers from an expired edge URL; never retry forever.
                    track.stream_retried = True
                    self.current = None
                    self._current_start_time = None
                    self._queue_track_for_retry(track)
                    await self._notify(content='⚠️ The stream was rejected; refreshing it once and retrying.')
                else:
                    track.stream_retried = False
                    self._remove_queued_track(track)
                    self.current = None
                    self._current_start_time = None
                    await self._notify(
                        content='❌ I could not stream that track. Please try it again later or choose another result.'
                    )
            else:
                track.stream_retried = False
            self._track_ended()

    def play_next_song(self, error=None, *, source: YTDLSource | None = None, track: Track | None = None):
        """Advance the queue once the current track finishes.

        discord.py invokes this from its AudioPlayer *thread* (discord/player.py's
        ``AudioPlayer.run`` calls ``_call_after`` in a ``finally``), not from the event
        loop -- and ``asyncio.Event.set`` is not thread-safe, so it has to be
        marshalled back onto the loop.  Calling it directly happened to work most of
        the time, which is the worst kind of bug.

        ``_retired_sources`` is read cross-thread too; it is safe because
        ``restart_current`` adds to it *before* calling ``voice.stop()`` and it is
        matched by identity, so it does not matter when the callback lands.
        """
        if source is not None and source in self._retired_sources:
            # The callback of a source restart_current replaced; not a track end.
            self._retired_sources.discard(source)
            return
        if self.manual_stop:
            self.manual_stop = False
            return
        diagnostics = source.stderr_tail if source else ''
        # discord.py calls this *before* source.cleanup() records FFmpeg's exit
        # code, so ``error`` is almost always None for an FFmpeg failure and the
        # stderr text is the only evidence.  A track that "ends" moments after
        # it started, with nobody asking it to, is the other giveaway: without
        # that check a broken filter chain drained the whole queue in silence.
        ended_early = (
            track is not None
            and track.duration_seconds > MIN_TRACK_SECONDS_FOR_EARLY_END
            and self.elapsed < EARLY_END_SECONDS
            and not (self._skip_requested or self._halt_requested)
        )
        is_stream_failure = bool(error) or ended_early or any(
            marker in diagnostics.lower() for marker in FFMPEG_FAILURE_MARKERS
        )
        if is_stream_failure:
            detail = str(error) if error else diagnostics
            print(f"[FFmpeg Error] {detail}")
            if track is not None:
                # Attribute replacement is atomic under CPython and happens
                # before the loop is woken below.  Only the event-loop task
                # reads and clears it.
                self._playback_failure = {'track': track, 'diagnostics': diagnostics or detail}
        try:
            self.bot.loop.call_soon_threadsafe(self.next.set)
        except RuntimeError:
            # The loop is already closed (shutdown); nothing left to advance.
            pass

    def skip(self):
        """End the current track and move on -- even under loop=track."""
        self.skip_votes.clear()
        if self.is_playing:
            self._skip_requested = True
            self.voice.stop()

    def halt(self) -> bool:
        """Stop playing and clear the queue, but stay connected.

        Returns whether anything was playing.  Before this, /stop cleared the
        queue and stopped the source, and the loop then replayed the current
        track under loop=track, or refilled the queue from autoplay.
        """
        self.clear_queue()
        self.skip_votes.clear()
        if not self.is_playing:
            self.current = None
            return False
        self._halt_requested = True
        self.voice.stop()
        return True

    async def stop(self, *, cancel_player: bool = True):
        """Clear the queue and disconnect.

        ``cancel_player=False`` is for callers running *inside* audio_player_task --
        the idle-timeout path did this and ended up cancelling and awaiting its own
        task, which only worked because the CancelledError happened to be delivered
        at that await and swallowed below.
        """
        if self._stopping:
            return
        self._stopping = True
        try:
            await self._stop(cancel_player=cancel_player)
        finally:
            self._stopping = False

    async def _stop(self, *, cancel_player: bool):
        self.songs.clear()
        self._loop_mode = 'off'
        self.manual_stop = False
        self._playback_failure = None
        self._pending_seek = None
        self.current = None
        self._last_track = None
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
        self.changed()

    def vote_skip(self, voter) -> tuple[str, str]:
        """Apply one /skip (or skip-button) request.  Returns (outcome, message).

        outcome is 'skipped', 'voted', 'already_voted' or 'not_listening'.
        """
        listening = voter.voice.channel if getattr(voter, 'voice', None) else None
        if self.voice and self.voice.channel and listening != self.voice.channel:
            return 'not_listening', "Join the voice channel Zephyr is in to vote."
        # Track carries requester_id, never a Member -- a Member is not
        # serializable, which is the whole reason the queue is plain data.
        if self.current is not None and voter.id == self.current.requester_id:
            self.skip()
            return 'skipped', '⏭️ Skipped.'
        if voter.id in self.skip_votes:
            return 'already_voted', "You have already voted to skip this song."
        self.skip_votes.add(voter.id)
        total, needed = len(self.skip_votes), self._skip_threshold()
        if total >= needed:
            self.skip()
            return 'skipped', '⏭️ Skip vote passed.'
        return 'voted', f'🗳️ Skip vote added ({total}/{needed})'

    def _skip_threshold(self):
        if not self.voice or not self.voice.channel:
            return 1
        non_bot = [m for m in self.voice.channel.members if not m.bot]
        return max(1, math.ceil(len(non_bot) / 2))

    async def restart_current(self, interaction: discord.Interaction = None, preserve_position: bool = True,
                              *, position: float | None = None) -> bool:
        """Recreate and restart the current track (used by effects and seek).

        ``position`` is an explicit seek target.  It is read inside the lock: a
        seek used to travel through ``_current_position``, which a restart
        already in progress overwrote with its own old position, so the seek
        was silently lost.

        Returns False when there was nothing to restart because the track is
        still loading.  A seek is then kept as ``_pending_seek`` and effects are
        picked up when the load finishes, so callers can say so honestly.

        Serialised: two quick effect toggles used to overlap, and the second
        ``voice.play()`` raised "Already playing audio" while the first source
        was still playing -- the error path then woke the player loop, which
        advanced the queue underneath a track that was still audible.

        A paused track stays paused: restarting it used to start playback again
        while the dashboard still showed it paused.
        """
        async with self._restart_lock:
            # A source, not just a current track: while the next track is still
            # loading there is nothing to restart, and playing one here raced the
            # player loop's own voice.play() ("Already playing audio").
            if not self.is_playing or self.source is None:
                if position is not None and self.current is not None:
                    self._pending_seek = max(0.0, position)
                return False
            was_paused = bool(self.voice and self.voice.is_paused())
            if position is not None:
                elapsed = max(0.0, position)
            else:
                elapsed = self.elapsed if preserve_position else 0.0
            track = self.current
            # Retire the old source *before* voice.stop(), so its after-callback --
            # whenever the player thread gets round to it -- is recognised and
            # ignored instead of advancing the queue.  A set, because a second
            # restart can retire another source before the first callback lands.
            if self.source is not None:
                self._retired_sources.add(self.source)
            self.voice.stop()
            try:
                # The old source is reaped by discord.py: AudioPlayer.run cleans it up in a
                # finally block once voice.stop() ends the player thread.
                source = await YTDLSource.from_track(track,
                                                     ffmpeg_options=self.get_ffmpeg_options(seek_position=elapsed),
                                                     volume=self._volume, loop=self.bot.loop)
            except Exception:
                # Nothing is playing now and the old callback is being ignored, so
                # nobody else will wake audio_player_task: without this it waited on
                # self.next forever and the guild stayed silent until /leave.
                self.source = None
                self.next.set()
                raise

            if (self._skip_requested or self._halt_requested or self.current is not track
                    or not self.voice or not self.voice.is_connected()):
                # Skipped, stopped or disconnected while the lookup ran: the skip
                # found nothing playing to stop, so finish it here.
                source.cleanup()
                self.source = None
                self.next.set()
                return True

            try:
                self.source = source
                self._current_position = elapsed
                self._current_start_time = time.time()
                self._current_rate = self.playback_rate()
                self.voice.play(source, after=lambda error: self.play_next_song(error, source=source, track=track))
                if was_paused:
                    self.voice.pause()
                    self._current_start_time = None
            except Exception:
                source.cleanup()
                self.source = None
                self.next.set()
                raise
            return True


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_states = {}
        self._voice_connect_locks = {}
        # guild id -> DJ role id.  Absent means "no DJ role configured", which is
        # a different rule rather than a stricter one; see _authorize.
        self._dj_role_ids: dict[int, str] = {}
        # guild id -> {"default_volume": int|None, "music_channel_ids": [str]},
        # from the dashboard's settings page.  Absent means bot defaults.
        self._music_settings: dict[int, dict] = {}
        self.sp = build_spotify_client(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Every music command needs a guild, and the guild's music channels.

        No guild: in a DM ctx.guild.id raised.  Music channels: the dashboard
        saved them but nothing enforced them.  Members who can manage the
        server are exempt, so a misconfigured list cannot lock admins out.
        """
        if interaction.guild is None:
            await interaction.response.send_message("Music commands only work in a server.", ephemeral=True)
            return False
        allowed = self._music_settings.get(interaction.guild.id, {}).get('music_channel_ids') or []
        permissions = getattr(interaction, 'permissions', None)
        if allowed and str(interaction.channel_id) not in allowed and not (permissions and permissions.manage_guild):
            mentions = ', '.join(f'<#{channel_id}>' for channel_id in allowed[:10])
            await interaction.response.send_message(f"Music commands are limited to {mentions} here.", ephemeral=True)
            return False
        return True

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Answer every failed command, not just failed checks.

        Only CheckFailure used to be handled, so anything else -- a voice
        connect that timed out, a bug -- left the user looking at "Zephyr is
        thinking..." until Discord gave up on the interaction.
        """
        if (type(error) is app_commands.CheckFailure and interaction.response.is_done()):
            # interaction_check already told the user why (a DM, or a channel
            # outside the music channels); a second "You can't use that here"
            # follow-up was pure noise.
            return
        if isinstance(error, app_commands.CheckFailure):
            message = ("❌ You need the **Manage Server** permission for that."
                       if isinstance(error, app_commands.MissingPermissions)
                       else "❌ You can't use that here.")
        else:
            original = getattr(error, 'original', error)
            if isinstance(original, (VoiceError, YTDLError)):
                message = f'❌ {original}'
            else:
                traceback.print_exception(type(original), original, original.__traceback__)
                message = '❌ Something went wrong running that command.'
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception as exc:
            print(f"[Music] Could not report a command error: {exc}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Tear the player down when the bot itself is disconnected from voice.

        Without this a kick (or a channel deletion) left audio_player_task
        running: every queued track was extracted, failed with "Not connected
        to voice", retried once and dropped, two error messages apiece.
        """
        if self.bot.user is None or member.id != self.bot.user.id:
            return
        if before.channel is None or after.channel is not None:
            return
        state = self.peek_voice_state(member.guild.id)
        if state is None or state.voice is None:
            return
        await state.stop()
        state.exists = False

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Forget everything about a guild the bot was removed from.

        Nothing released per-guild state before: a 24/7 player in a guild that
        kicked the bot kept its task running for the life of the process.
        """
        state = self.voice_states.pop(int(guild.id), None)
        self._voice_connect_locks.pop(int(guild.id), None)
        self._dj_role_ids.pop(int(guild.id), None)
        self._music_settings.pop(int(guild.id), None)
        if state is not None:
            try:
                await state.stop()
            except Exception as exc:
                print(f"[Music] Could not stop the player for removed guild {guild.id}: {exc}")
            state.exists = False
        await self._clear_snapshot(guild.id)

    def _get_voice_lock(self, guild_id: int) -> asyncio.Lock:
        return self._voice_connect_locks.setdefault(guild_id, asyncio.Lock())

    def peek_voice_state(self, guild_id: int) -> VoiceState | None:
        """The live state for a guild, or None. Never creates one.

        Callers with no Context (the Redis bridge) need to be able to answer
        "nothing is playing" rather than silently spinning up a state.
        """
        state = self.voice_states.get(int(guild_id))
        return state if state and state.exists else None

    def ensure_voice_state(self, guild_id: int, *, channel_id: int | None = None,
                           sticky: bool = True) -> VoiceState:
        """Get or create the state for a guild, refreshing the sticky channel.

        ``sticky=False`` uses ``channel_id`` only for a brand-new state: /queue
        or /lyrics typed in another channel must not move where now-playing
        messages go -- only commands that start music do.
        """
        state = self.voice_states.get(int(guild_id))
        if not state or not state.exists:
            state = VoiceState(self.bot, guild_id, channel_id=channel_id)
            default_volume = self._music_settings.get(int(guild_id), {}).get('default_volume')
            if default_volume is not None:
                # The dashboard stores 0-200 (percent); the player uses a factor.
                state._volume = max(0, min(int(default_volume), 200)) / 100
            state.on_change = self.publish_state
            state.np_view_factory = functools.partial(NowPlayingView, self, int(guild_id))
            self.voice_states[int(guild_id)] = state
        elif channel_id is not None and (sticky or state.np_channel_id is None):
            state.np_channel_id = channel_id
        return state

    async def _live_state(self, state: VoiceState, guild, destination, *,
                          channel_id: int | None = None) -> VoiceState:
        """The state to queue into after a slow lookup.

        A search or playlist read can take up to a minute, and the idle timeout
        may retire the player meanwhile.  Queueing into the retired state said
        "Enqueued" for a track that never played, so a fresh one is connected
        instead.
        """
        if state.exists:
            state.start_player()
            return state
        fresh = self.ensure_voice_state(guild.id, channel_id=channel_id if channel_id is not None
                                        else state.np_channel_id, sticky=True)
        await self._connect(fresh, guild, destination)
        self._ensure_np_channel(fresh, guild)
        fresh.start_player()
        return fresh

    def get_voice_state(self, ctx: commands.Context, *, sticky: bool = False):
        """Context-flavoured wrapper, so the existing command call sites are unchanged."""
        channel_id = ctx.channel.id if getattr(ctx, 'channel', None) else None
        return self.ensure_voice_state(ctx.guild.id, channel_id=channel_id, sticky=sticky)

    def cog_unload(self):
        self._snapshot_loop.cancel()
        self._settings_loop.cancel()
        self._now_playing_loop.cancel()
        for guild_id in list(self.voice_states):
            self.bot.loop.create_task(self._clear_snapshot(guild_id))
        for state in self.voice_states.values():
            self.bot.loop.create_task(state.stop())

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
            print(f"[Music] Could not clear the player snapshot for {guild_id}: {exc}")

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
            print(f"[Music] Could not publish the player snapshot for {guild_id}: {exc}")

    @tasks.loop(seconds=3)
    async def _snapshot_loop(self):
        """Keep the snapshot fresh for changes made in Discord.

        Three seconds to match the dashboard's poll interval; a slower loop would
        make a /volume typed in Discord take up to two polls to appear.  Only
        guilds with a live state are published -- an idle bot writes nothing.
        """
        for guild_id in list(self.voice_states):
            state = self.peek_voice_state(guild_id)
            if state is None:
                continue
            if state.voice is None and state.current is None and not len(state.songs):
                # A state created by a read-only command (/queue, /volume...)
                # in a guild that never played anything.  Publishing it every
                # three seconds forever was exactly the "idle bot writes" case.
                continue
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

        # The now-playing buttons call the unwrapped handler and publish after
        # answering the interaction; see NowPlayingView._run.
        wrapper.__wrapped__ = handler
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

    @staticmethod
    def _member_of(guild, actor_id):
        """The cached member for ``actor_id``, or None -- never an API fetch."""
        if guild is None:
            return None
        try:
            return guild.get_member(int(actor_id))
        except (TypeError, ValueError):
            return None

    def _may_force_skip(self, state: 'VoiceState', member) -> bool:
        """Who skips at once rather than voting: the requester, a DJ, a manager."""
        if state.current is not None and member.id == state.current.requester_id:
            return True
        permissions = getattr(member, 'guild_permissions', None)
        if permissions is not None and permissions.manage_guild:
            return True
        dj_role_id = self._dj_role_ids.get(int(state.guild_id)) if getattr(state, 'guild_id', None) else None
        if dj_role_id:
            return any(str(role.id) == str(dj_role_id) for role in getattr(member, 'roles', []))
        return False

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
            print(f"[Music] Could not read DJ roles: {exc}")

    async def reload_music_settings(self) -> None:
        """Refresh the cached default volume and music channels."""
        try:
            self._music_settings = {
                int(guild_id): values
                for guild_id, values in (await asyncio.to_thread(read_music_settings)).items()
            }
        except Exception as exc:
            print(f"[Music] Could not read music settings: {exc}")

    @tasks.loop(minutes=10)
    async def _settings_loop(self):
        await self.reload_dj_roles()
        await self.reload_music_settings()

    @_settings_loop.before_loop
    async def _before_settings_loop(self):
        await self.bot.wait_until_ready()

    async def _bridge_reload_settings(self, guild, actor_id, args):
        await self.reload_dj_roles()
        await self.reload_music_settings()
        return {'reloaded': True}

    def _require_state(self, guild_id) -> VoiceState:
        state = self.peek_voice_state(guild_id)
        if state is None or not state.voice or not state.voice.is_connected():
            raise VoiceError("Zephyr is not connected to a voice channel.")
        return state

    async def _bridge_state(self, guild, actor_id, args):
        if guild is None:
            # str(guild.id) raised AttributeError here, and its text reached the
            # dashboard as the error message.
            raise VoiceError("Zephyr is not in that server.")
        state = self.peek_voice_state(guild.id)
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
        state = self.peek_voice_state(guild.id) if guild is not None else None
        member = self._member_of(guild, actor_id)
        # Whoever queued the song may always skip it (README, PRD), even when a
        # DJ role gates every other control: the ⏭️ button used to refuse them.
        if not (state is not None and state.current is not None and member is not None
                and member.id == state.current.requester_id):
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
        state.halt()
        return {'stopped': True}

    async def _bridge_clear(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        removed = len(state.songs)
        state.clear_queue()
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
        position = max(0.0, position)
        # Passed explicitly rather than through _current_position, which a
        # restart already in progress would overwrite.
        await state.restart_current(position=position)
        return {'position_s': position}

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

    @staticmethod
    def _locate(state: VoiceState, index: int, expect) -> int:
        """The queue position the browser meant, not just the one it sent.

        The dashboard's snapshot is up to a few seconds old.  If a track
        finished in that time every index shifted by one, and clicking × on
        row 3 removed what had been row 4.  The browser now says which track it
        means; the nearest matching row wins, and if the track is gone the
        request is refused rather than applied to a neighbour.
        """
        size = len(state.songs)
        if not isinstance(expect, dict):
            if not 0 <= index < size:
                raise VoiceError("That track is not in the queue.")
            return index

        want_url = expect.get('url') or None
        want_title = str(expect.get('title') or '')

        def matches(track: Track) -> bool:
            if want_url is not None:
                return track.url == want_url
            return track.title == want_title

        if 0 <= index < size and matches(state.songs[index]):
            return index
        candidates = [position for position, track in enumerate(state.songs) if matches(track)]
        if not candidates:
            raise VoiceError("The queue changed and that track is no longer in it.")
        return min(candidates, key=lambda position: abs(position - index))

    async def _bridge_jump(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        index = self._locate(state, int(_coerce_float(args.get('index'), 'index')), args.get('expect'))
        state.songs.move(index, 0)
        state.skip()
        return {'jumped_to': index}

    async def _bridge_remove(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        index = self._locate(state, int(_coerce_float(args.get('index'), 'index')), args.get('expect'))
        removed = state.songs[index].title
        state.songs.remove(index)
        return {'removed': removed}

    async def _bridge_move(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        source = self._locate(state, int(_coerce_float(args.get('from'), 'from')), args.get('expect'))
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

        await self._connect(state, guild, destination)
        self._ensure_np_channel(state, guild)
        state.start_player()

        tracks = await self._resolve_query(query, requester_id=member.id, requester_mention=member.mention)
        if not tracks:
            raise VoiceError("Nothing found for that query.")
        _refuse_past_deadline()
        state = await self._live_state(state, guild, destination)
        if str(args.get('mode')) == 'next':
            for track in reversed(tracks):
                state.songs.add_to_front(track)
        else:
            for track in tracks:
                state.songs.put_nowait(track)
        return {'added': len(tracks), 'title': tracks[0].title}

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        await ctx.send(embed=discord.Embed(description=f'An error occurred: {str(error)}', color=discord.Color.red()))

    @staticmethod
    async def _interaction_ctx(interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        return ctx

    def _require_voice(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            raise VoiceError("You are not connected to any voice channel.")

    async def _resolve_query(self, query: str, *, requester_id: int, requester_mention: str) -> list[Track]:
        """Turn whatever a user typed into queue entries.  Shared by /play and the dashboard.

        Spotify links are read as **metadata only**: a title and a duration per
        track, with no YouTube lookup.  ``YTDLSource.from_track`` already
        searches by title when a track with no URL comes up -- which is how
        imported playlists work -- so each track costs a lookup only if it is
        actually played.  The old path made a Spotify call, a YouTube search
        and an extraction per track up front, so a 200-track playlist took
        longer than Discord's 15-minute interaction token lasts.  The dashboard
        handed Spotify links straight to yt-dlp, which cannot read them at all.
        """
        query = _drop_auto_mix(_sanitize_search(query))
        if not _is_spotify_url(query):
            return await YTDLSource.resolve_tracks(
                query, requester_id=requester_id, requester_mention=requester_mention, loop=self.bot.loop)

        if self.sp is None:
            raise YTDLError("Spotify links are not available on this bot.")
        try:
            _, rows = await asyncio.wait_for(
                asyncio.to_thread(fetch_playlist_metadata, self.sp, query), timeout=SPOTIFY_TIMEOUT_SECONDS)
        except ValueError as exc:
            raise YTDLError(str(exc)) from None
        except asyncio.TimeoutError:
            raise YTDLError("Spotify took too long to answer.") from None
        except Exception as exc:
            LOGGER.warning("Spotify lookup failed for %s: %s", query, exc)
            raise YTDLError("Spotify could not read that link. Private playlists cannot be played.") from None
        if not rows:
            raise YTDLError("That Spotify link has no playable tracks.")
        return [
            Track(title=row['title'], url=None, duration_seconds=row['duration_s'] or 0,
                  requester_id=requester_id, requester_mention=requester_mention, source='spotify')
            for row in rows
        ]

    async def _gate(self, interaction: discord.Interaction) -> bool:
        """Apply the player permission rule to a slash command.

        The docs promise the DJ role governs the player, but only the buttons
        and the dashboard went through ``_authorize``: in Discord anyone could
        /volume 1000 or /clear, while /pause needed Manage Server, which a DJ
        does not have.  Every player-changing command now uses the same rule.
        """
        try:
            self._authorize(interaction.guild, interaction.user.id)
        except VoiceError as exc:
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True)
            return False
        return True

    async def _connect(self, state: VoiceState, guild: discord.Guild, destination) -> None:
        """Make sure ``state`` owns a live voice connection, joining ``destination`` if not.

        The one place the player connects.  Four call sites used to do this
        four ways, and the ones that went straight to ``destination.connect()``
        raised "Already connected to a voice channel" whenever the guild
        already had a voice client the state did not know about -- after /say,
        or after a state was recreated.  An existing client is adopted (moved
        if it is elsewhere), and a half-dead one is torn down first.
        """
        async with self._get_voice_lock(guild.id):
            if state.voice and state.voice.is_connected():
                return
            vc = guild.voice_client
            if vc and not vc.is_connected():
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
                vc = None
            try:
                if vc:
                    if vc.channel != destination:
                        await vc.move_to(destination)
                    state.voice = vc
                else:
                    state.voice = await destination.connect(self_deaf=True, timeout=30.0, reconnect=True)
            except Exception as exc:
                raise VoiceError(f"Could not join {destination}: {exc}") from exc

    def _ensure_np_channel(self, state: VoiceState, guild: discord.Guild) -> None:
        """Give a state started from the dashboard somewhere to post.

        A slash command sets the sticky channel; the bridge has no channel, so
        playback started from the web announced nothing and its errors went
        nowhere.  The first configured music channel is the natural home.
        """
        if state.np_channel_id is not None:
            return
        for channel_id in self._music_settings.get(int(guild.id), {}).get('music_channel_ids') or []:
            try:
                channel = guild.get_channel(int(channel_id))
            except (TypeError, ValueError):
                continue
            if channel is not None:
                state.np_channel_id = channel.id
                return

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
        state = self.get_voice_state(ctx, sticky=True)
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
                if state.current is not None:
                    # Moving would take the music away from everyone listening.
                    try:
                        self._authorize(interaction.guild, interaction.user.id)
                    except VoiceError:
                        await interaction.followup.send(embed=discord.Embed(
                            description=f'❌ Zephyr is playing in {vc.channel}. Ask a DJ or a server manager to move it.',
                            color=discord.Color.red()))
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
        state = self.get_voice_state(ctx, sticky=True)
        if not channel and not interaction.user.voice:
            await interaction.response.send_message("You are neither connected to a voice channel nor specified a channel to join.", ephemeral=True)
            return
        destination = channel or interaction.user.voice.channel
        await interaction.response.defer()
        if state.voice and state.voice.is_connected():
            async with self._get_voice_lock(interaction.guild.id):
                await state.voice.move_to(destination)
            await interaction.followup.send(embed=discord.Embed(description=f'➡️ Moved to {destination}', color=discord.Color.green()))
        else:
            await self._connect(state, interaction.guild, destination)
            await interaction.followup.send(embed=discord.Embed(description=f'🔊 Joined {destination}', color=discord.Color.green()))

    @app_commands.command(name='leave', description='Clears the queue and leaves the voice channel.')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def leave(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        # Defer first: stop() cancels the player and disconnects, which can
        # outlast the three seconds Discord allows for a first response.
        await interaction.response.defer()
        await state.stop()
        self.voice_states.pop(ctx.guild.id, None)
        await interaction.followup.send(embed=discord.Embed(description='👋 Left voice channel.', color=discord.Color.green()))

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
        # /playskip ends what everyone is hearing and /playnext reorders the queue:
        # both change playback for others, so they follow the DJ rule like /skip's
        # instant path, /move and /jump.  /play only appends and stays open to all.
        if mode != 'normal' and not await self._gate(interaction):
            return

        await interaction.response.defer()
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx, sticky=True)
        destination = interaction.user.voice.channel
        await self._connect(state, interaction.guild, destination)
        state.start_player()

        async def _edit_status(description: str):
            # Titles and error text can carry most of a 6,000-character query;
            # past 4,096 Discord refused the reply and the user saw nothing useful.
            embed = discord.Embed(description=_short(description, 4000), color=discord.Color.blurple())
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

            if is_playlist_input:
                await _edit_status('🔎 Resolving playlist, this may take a moment...')
            songs = await self._resolve_query(
                search, requester_id=requester_id, requester_mention=requester_mention)

            if not songs:
                await _edit_status('❌ No tracks found.')
                return
            state = await self._live_state(state, interaction.guild, destination,
                                           channel_id=interaction.channel_id)

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
            traceback.print_exc()
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
        view = View(timeout=60)
        message: discord.Message | None = None

        async def _disable(content: str | None = None):
            select.disabled = True
            view.stop()
            if message is None:
                return
            try:
                await message.edit(content=content or message.content, view=view)
            except discord.HTTPException:
                pass

        async def select_callback(interaction2: discord.Interaction):
            if interaction2.user.id != interaction.user.id:
                await interaction2.response.send_message("Run `/msearch` yourself to pick a track.", ephemeral=True)
                return
            await interaction2.response.defer()
            # One pick per search, and a copy: the same Track object queued twice
            # made a failed retry of one copy delete the other (matched by identity).
            track = copy.copy(results[int(select.values[0])])
            await _disable(f"🔎 Picked: {track}")
            # Component callbacks never reach cog_app_command_error, so a failed
            # connect used to vanish, and a refused enqueue still said "Selected".
            try:
                queued = await self._enqueue_track(interaction2, track)
            except (VoiceError, YTDLError) as exc:
                await interaction2.followup.send(f'❌ {exc}', ephemeral=True)
                return
            except Exception:
                traceback.print_exc()
                await interaction2.followup.send('❌ Something went wrong queueing that track.', ephemeral=True)
                return
            if queued:
                await interaction2.followup.send(embed=discord.Embed(description=f'🎶 Selected: {track}', color=discord.Color.green()), ephemeral=True)

        async def on_timeout():
            await _disable("🔎 Search expired — run `/msearch` again.")

        select.callback = select_callback
        view.on_timeout = on_timeout
        view.add_item(select)
        message = await interaction.followup.send("🔎 Search results:", view=view, wait=True)

    async def _enqueue_track(self, interaction: discord.Interaction, track: Track) -> bool:
        """Queue one track; False (after telling the user why) if it was refused."""
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You need to be in a voice channel.", ephemeral=True)
            return False
        # Not Context.from_interaction: a select-menu interaction carries no
        # command data, so that raised and no /msearch pick was ever queued.
        state = self.ensure_voice_state(interaction.guild.id, channel_id=interaction.channel_id, sticky=True)
        await self._connect(state, interaction.guild, interaction.user.voice.channel)
        state.start_player()
        state.songs.put_nowait(track)
        return True

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
        # self.now is an app_commands.Command, which is not callable; awaiting
        # it raised TypeError on every /np.
        await self.now.callback(self, interaction)

    @app_commands.command(name='pause', description='Pauses the currently playing song.')
    async def pause(self, interaction: discord.Interaction):
        if not await self._gate(interaction):
            return
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
    async def resume(self, interaction: discord.Interaction):
        if not await self._gate(interaction):
            return
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if state.voice and state.voice.is_paused():
            state.voice.resume()
            state._current_start_time = time.time()
            await interaction.response.send_message(embed=discord.Embed(description='▶️ Resumed.', color=discord.Color.green()))
        else:
            await interaction.response.send_message("Nothing is currently paused.", ephemeral=True)

    @app_commands.command(name='stop', description='Stops playing and clears the queue.')
    async def stop(self, interaction: discord.Interaction):
        if not await self._gate(interaction):
            return
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if state.halt():
            await interaction.response.send_message(embed=discord.Embed(description='⏹️ Stopped and cleared queue.', color=discord.Color.red()))
        elif state.voice and state.voice.is_connected():
            await interaction.response.send_message(embed=discord.Embed(description='🧹 Queue cleared.', color=discord.Color.orange()))
        else:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)

    @app_commands.command(name='clear', description='Clears the queue but keeps the current song playing.')
    async def clear(self, interaction: discord.Interaction):
        if not await self._gate(interaction):
            return
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        state.clear_queue()
        await interaction.response.send_message(embed=discord.Embed(description='🧹 Queue cleared.', color=discord.Color.orange()))

    @app_commands.command(name='skip', description='Vote to skip the current song.')
    async def skip(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Not playing any music right now...", ephemeral=True)
            return
        if self._may_force_skip(state, interaction.user):
            # The requester, a DJ or a server manager skip at once, as README
            # and the ⏭️ button say; /skip used to make a DJ vote like anyone.
            state.skip()
            await interaction.response.send_message(
                embed=discord.Embed(description='⏭️ Skipped.', color=discord.Color.green()))
            return
        outcome, text = state.vote_skip(interaction.user)
        if outcome in ('not_listening', 'already_voted'):
            await interaction.response.send_message(text, ephemeral=True)
            return
        color = discord.Color.green() if outcome == 'skipped' else discord.Color.orange()
        await interaction.response.send_message(embed=discord.Embed(description=text, color=color))

    @app_commands.command(name='seek', description='Seeks to a position in the current track.')
    @app_commands.describe(position="Timestamp e.g. 1:30, 90, 2m")
    async def seek(self, interaction: discord.Interaction, position: str):
        if not await self._gate(interaction):
            return
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
            applied = await state.restart_current(interaction, position=pos)
            description = (f'⏩ Seeked to `{_format_timestamp(pos)}`.' if applied else
                           f'⏩ The track is still loading; it will start at `{_format_timestamp(pos)}`.')
            await interaction.followup.send(embed=discord.Embed(description=description, color=discord.Color.green()))
        except Exception as e:
            await interaction.followup.send(embed=discord.Embed(description=f'❌ Seek failed: {e}', color=discord.Color.red()))

    @app_commands.command(name='forward', description='Skips forward in the current track.')
    @app_commands.describe(amount="Time to skip forward e.g. 10s, 1:00")
    async def forward(self, interaction: discord.Interaction, amount: str):
        if not await self._gate(interaction):
            return
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
            applied = await state.restart_current(interaction, position=new_pos)
            description = (f'⏩ Forwarded `{_format_timestamp(delta)}`.' if applied else
                           f'⏩ The track is still loading; it will start at `{_format_timestamp(new_pos)}`.')
            await interaction.followup.send(embed=discord.Embed(description=description, color=discord.Color.green()))
        except Exception as e:
            await interaction.followup.send(embed=discord.Embed(description=f'❌ Forward failed: {e}', color=discord.Color.red()))

    @app_commands.command(name='rewind', description='Rewinds in the current track.')
    @app_commands.describe(amount="Time to rewind e.g. 10s, 1:00")
    async def rewind(self, interaction: discord.Interaction, amount: str):
        if not await self._gate(interaction):
            return
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
            applied = await state.restart_current(interaction, position=new_pos)
            description = (f'⏪ Rewound `{_format_timestamp(delta)}`.' if applied else
                           f'⏪ The track is still loading; it will start at `{_format_timestamp(new_pos)}`.')
            await interaction.followup.send(embed=discord.Embed(description=description, color=discord.Color.green()))
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
                            value=_short(f"{_link(state.current, 80)}\n"
                                         f"Requested by {state.current.requester_mention or 'Unknown'} • "
                                         f"`{_format_timestamp(state.current.duration_seconds)}`", 1024),
                            inline=False)
            embed.add_field(name='Loop Mode', value=state.loop.capitalize(), inline=True)
            embed.add_field(name='Volume', value=f"{int(state.volume * 100)}%", inline=True)

        # The list goes in the description (4,096 characters), not a field
        # (1,024): ten rows of YouTube links, long durations and mentions came to
        # ~1,500 characters, and Discord rejected the whole embed.
        upcoming = list(state.songs)[start:end]
        if upcoming:
            lines = [f'**Up Next** — {total} track{"s" if total != 1 else ""}']
            used = len(lines[0])
            for i, song in enumerate(upcoming, start=start):
                row = (f'`{i + 1:>2}.` {_link(song, 55)} · `{_format_timestamp(song.duration_seconds)}`'
                       f' · {song.requester_mention or "Unknown"}')
                if used + len(row) + 1 > 3900:
                    lines.append('*…*')
                    break
                lines.append(row)
                used += len(row) + 1
            embed.description = '\n'.join(lines)
        elif total == 0:
            embed.description = '**Up Next**\nNo more songs in queue.'

        total_duration = state.current.duration_seconds if state.is_playing else 0
        for song in state.songs:
            # Under loop=queue the playing track is already re-queued at the
            # back; counting it again overstated the total (as _queue_payload notes).
            if state.is_playing and song is state.current:
                continue
            total_duration += song.duration_seconds or 0
        embed.set_footer(text=f'Page {page}/{pages} • Total duration: {_format_timestamp(total_duration)}')
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='shuffle', description='Shuffles the queue.')
    async def shuffle(self, interaction: discord.Interaction):
        if not await self._gate(interaction):
            return
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if len(state.songs) == 0:
            await interaction.response.send_message("Empty queue.", ephemeral=True)
            return
        state.songs.shuffle()
        await interaction.response.send_message(embed=discord.Embed(description='🔀 Queue shuffled.', color=discord.Color.green()))

    @app_commands.command(name='remove', description='Removes one or more songs from the queue.')
    async def remove(self, interaction: discord.Interaction, index: int, count: int = 1):
        if not await self._gate(interaction):
            return
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
        # Every title used to be listed: /remove count:100 removed the tracks and
        # then failed to reply, past the 4,096-character description limit.
        shown = [f'• {_short(t, 80)}' for t in removed[:REMOVE_LIST_LIMIT]]
        if len(removed) > REMOVE_LIST_LIMIT:
            shown.append(f'…and {len(removed) - REMOVE_LIST_LIMIT} more')
        await interaction.response.send_message(embed=discord.Embed(
            description=f'🗑️ Removed **{len(removed)}** track(s):\n' + '\n'.join(shown),
            color=discord.Color.orange()))

    @app_commands.command(name='move', description='Moves a track to another position in the queue.')
    async def move(self, interaction: discord.Interaction, from_index: int, to_index: int):
        if not await self._gate(interaction):
            return
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
        if not await self._gate(interaction):
            return
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
        if not await self._gate(interaction):
            return
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
        if not await self._gate(interaction):
            return
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
        if not await self._gate(interaction):
            return
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
        if not await self._gate(interaction):
            return
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
        if not await self._gate(interaction):
            return
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
        if not await self._gate(interaction):
            return
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
        if not await self._gate(interaction):
            return
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
        if not await self._gate(interaction):
            return
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
        if not await self._gate(interaction):
            return
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
        upcoming = list(state.songs)
        if state.current is not None and state.loop == 'queue':
            # Queue-loop re-appends the current track to the queue as it starts,
            # so it is already in `upcoming`; listing it twice saved it twice.
            upcoming = [track for track in upcoming if track is not state.current]
        tracks = ([state.current] if state.current else []) + upcoming
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
            traceback.print_exc()
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
            traceback.print_exc()
            await interaction.followup.send(
                embed=discord.Embed(description=f'❌ Could not read your playlists: {exc}', color=discord.Color.red()))
            return
        if playlist is None:
            await interaction.followup.send(
                embed=discord.Embed(description=f'❌ No playlist called **{name}**.', color=discord.Color.red()))
            return

        added = await self._enqueue_playlist(interaction.guild, interaction.user,
                                             interaction.user.voice.channel, playlist,
                                             channel_id=interaction.channel_id)
        await interaction.followup.send(embed=discord.Embed(
            description=f'📀 Queued **{added}** track(s) from **{playlist["name"]}**.',
            color=discord.Color.green()))

    async def _enqueue_playlist(self, guild, member, destination, playlist: dict,
                                *, channel_id: int | None = None) -> int:
        """Connect if needed and append a stored playlist to the queue.

        Nothing is resolved here.  Stored rows become Tracks as they are -- some
        with only a title -- and ``YTDLSource.from_track`` does the lookup when
        each one actually plays.  Resolving 200 rows up front would take minutes
        and throw most of the work away the moment somebody ran /skip.
        """
        # /load passes the channel it was run in, as /play does: without one an
        # idle guild with no music channels configured had nowhere to announce,
        # so the session posted no now-playing, no buttons and no errors.
        if channel_id is not None:
            state = self.ensure_voice_state(guild.id, channel_id=channel_id, sticky=True)
        else:
            state = self.ensure_voice_state(guild.id)
        await self._connect(state, guild, destination)
        # A slow voice connect could outlast the dashboard's wait; loading the
        # playlist after its "did not respond" made a retry load it twice.
        _refuse_past_deadline()
        self._ensure_np_channel(state, guild)
        state.start_player()

        for row in playlist['tracks']:
            state.songs.put_nowait(Track(
                title=row['title'], url=row['url'], duration_seconds=row['duration_s'] or 0,
                requester_id=member.id, requester_mention=member.mention,
                # Rows saved before sources were checked may say anything.
                source=clean_source(row['source'], row['url'])))
        state.changed()
        return len(playlist['tracks'])

    @app_commands.command(name='playlists', description='Lists your saved playlists.')
    async def playlists(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id) if interaction.guild else None
        try:
            saved = await asyncio.to_thread(list_playlists, str(interaction.user.id), guild_id=guild_id)
        except Exception as exc:
            traceback.print_exc()
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
            traceback.print_exc()
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
        # Your own playlists load anywhere; someone else's only when it is public
        # *and* was shared in this guild -- otherwise sequential ids would let
        # anyone pull other servers' public playlists.
        if playlist['owner_id'] != str(member.id) and not (
            playlist['is_public'] and str(playlist.get('guild_id') or '') == str(guild.id)
        ):
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
        state._autoplay_enabled = _coerce_bool(args.get('enabled', False), 'enabled')
        return {'autoplay': state._autoplay_enabled}

    # ---------------- Spotify Helpers ----------------

    # Kept as aliases for anything that still reaches them through the cog;
    # resolution itself is _resolve_query, via zephyr/services/spotify.py.
    _resolve_spotify_short_link = staticmethod(_resolve_spotify_short_link)
    _parse_spotify_id = staticmethod(_parse_spotify_id)

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
                # safe="" so a "/" in a title (AC/DC) is escaped instead of splitting the path.
                search_url = f"https://api.lyrics.ovh/suggest/{quote(title, safe='')}"
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
                        lyrics_url = f"https://api.lyrics.ovh/v1/{quote(artist, safe='')}/{quote(song_title, safe='')}"
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
            # Embed titles stop at 256; a long "feat." chain broke the first part.
            embed = discord.Embed(title=_short(f'🎤 Lyrics — {title}', 256) if idx == 0 else None,
                                  description=chunk,
                                  color=discord.Color.teal())
            embed.set_footer(text=f"Part {idx + 1}/{len(chunks)}")
            if idx == 0:
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
