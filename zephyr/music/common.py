"""URL and query helpers, the tuning constants, and the two exceptions.

Everything here was module-level in ``zephyr/cogs/music.py`` and is imported by
at least two of the modules that came out of it, so it lands in one place rather
than being duplicated or creating a cycle.
"""

import re


from zephyr.core.logging import get_logger
from zephyr.services.spotify import (
    is_spotify_url,
    parse_spotify_id,
    resolve_short_link,
)

log = get_logger(__name__)


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

# Half the listeners, which is what /skip has always used. Now a default rather
# than a constant: a two-person server wants 1, and a hundred-person stage does
# not want 50 people to agree before a bad track ends.
DEFAULT_SKIP_RATIO = 0.5
MIN_SKIP_RATIO = 0.05
MAX_SKIP_RATIO = 1.0

# The commands the DJ lock does *not* cover, as an exemption list rather than a
# list of what it does cover. Deriving the locked set as the complement is
# fail-closed: a music command added later is locked by default, and the failure
# mode of that mistake is "a DJ had to press it", not "the lock silently did not
# apply to the new command".
DJ_EXEMPT_COMMANDS = frozenset({
    "now", "np", "queue", "lyrics", "playlists", "playlist-delete", "save",
})


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
