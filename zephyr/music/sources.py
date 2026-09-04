"""``Track`` -- what the queue holds -- and ``YTDLSource`` -- what plays it.

``Track`` is plain data on purpose: the queue is serialised into a Redis
snapshot for the dashboard, and a ``discord.Member`` is not serialisable, which
is why it carries ``requester_id`` and ``requester_mention`` rather than a
member object.
"""

import asyncio
import dataclasses
import functools

import discord
import yt_dlp

from zephyr.core.ffmpeg import FFMPEG_PATH
from zephyr.core.logging import get_logger
from zephyr.music.common import (
    YTDLError,
    _format_duration,
    _is_audio_file_url,
    _is_url,
    _sanitize_search,
)
from zephyr.utils import embeds
from zephyr.utils.time_utils import _format_timestamp

log = get_logger(__name__)


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
        embed = embeds.info(
            f'```css\n{self.title}\n```', title='🎵 Now playing'
        )
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
            embed.set_footer(
                text=embeds.footer_text('Original video unavailable — playing a re-resolved match.'),
                icon_url=embeds.icon_url(),
            )
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
