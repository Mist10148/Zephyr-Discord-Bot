"""Spotify link parsing and metadata lookup, shared by the bot and the web tier.

Extracted from ``zephyr/cogs/music.py`` so the dashboard's playlist importer can
use it: importing that module from Flask would drag in discord.py, yt-dlp and a
voice stack to parse a URL.  ``spotipy`` is imported lazily for the same reason
-- building a Flask app must not require a Spotify client.

The import path deliberately fetches **metadata only**.  It never touches
yt-dlp, so bringing in a 200-track playlist is two Spotify calls rather than 200
extractions, and the resulting rows carry no URL at all.  Resolution happens
once, at play time, in ``YTDLSource.from_track`` -- for the tracks that are
actually played.
"""

import re
from urllib.parse import urlsplit

# Matches the plan's cap on playlist size, which yt-dlp's max_entries also uses.
MAX_IMPORT_TRACKS = 200


# Hosts a Spotify link may live on.  Matched exactly against the parsed host:
# the old substring test accepted ``https://10.0.0.5/?spotify.link``, which was
# then fetched (with redirects) by resolve_short_link -- a blind request to any
# internal address from anyone with /play or a dashboard session.
SPOTIFY_HOSTS = frozenset({"open.spotify.com", "play.spotify.com", "spotify.com", "www.spotify.com"})
SHORT_LINK_HOSTS = frozenset({"spotify.link", "spotify.app.link"})
MAX_SHORT_LINK_HOPS = 5


def _host(value: str) -> str | None:
    candidate = (value or "").strip().strip("<>")
    if not candidate.lower().startswith(("http://", "https://")):
        return None
    try:
        return (urlsplit(candidate).hostname or "").lower() or None
    except ValueError:
        return None


def is_short_link(value: str) -> bool:
    return _host(value) in SHORT_LINK_HOSTS


def is_spotify_url(value: str) -> bool:
    candidate = (value or "").strip().strip("<>").lower()
    if candidate.startswith("spotify:"):
        return True
    host = _host(candidate)
    return host in SPOTIFY_HOSTS or host in SHORT_LINK_HOSTS


def parse_spotify_id(url: str, kind: str) -> str | None:
    """Extract a Spotify ID from a web URL or a ``spotify:`` URI."""
    if url.startswith("spotify:"):
        parts = url.split(":")
        if len(parts) >= 3 and parts[1] == kind:
            return parts[2].split("?")[0]
        return None
    match = re.search(rf"/{kind}/([^/?#]+)", url)
    return match.group(1) if match else None


def resolve_short_link(url: str) -> str:
    """Follow a spotify.link redirect to the real open.spotify.com URL."""
    import requests

    from urllib.parse import urljoin

    if not is_short_link(url):
        return url
    # Followed by hand: with allow_redirects=True every hop was fetched before
    # anything looked at it, so a short link that bounced through an internal
    # address made a blind request from the bot and the website.  Only Spotify
    # hosts are followed; the first open.spotify.com URL is the answer.
    current = url
    try:
        for _ in range(MAX_SHORT_LINK_HOPS):
            response = requests.head(current, allow_redirects=False, timeout=10)
            location = response.headers.get("Location") if response.is_redirect else None
            if not location:
                break
            current = urljoin(current, location)
            if current.lower().startswith("spotify:") or _host(current) in SPOTIFY_HOSTS:
                return current
            if _host(current) not in SHORT_LINK_HOSTS:
                break
    except Exception as exc:
        print(f"[Spotify Resolve Error] {exc}")
    return url


def build_client(client_id: str | None, client_secret: str | None):
    """A client-credentials Spotify client, or None when unconfigured.

    None rather than a raise: a deployment without Spotify credentials is
    supported, and the caller turns that into a clear 503 instead of a stack
    trace.
    """
    if not client_id or not client_secret:
        return None
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials

    return spotipy.Spotify(
        client_credentials_manager=SpotifyClientCredentials(
            client_id=client_id, client_secret=client_secret
        )
    )


def _track_title(track: dict) -> str:
    artists = ", ".join(artist.get("name", "") for artist in track.get("artists") or [] if artist.get("name"))
    name = track.get("name") or ""
    return f"{artists} - {name}".strip(" -") or name


def fetch_playlist_metadata(client, url: str, *, limit: int = MAX_IMPORT_TRACKS) -> tuple[str, list[dict]]:
    """Return ``(name, tracks)`` for a Spotify track, album or playlist URL.

    Tracks are ``{title, url: None, duration_s, source}``.  ``url`` is None
    because nothing has been resolved -- that is the whole point of the import.
    """
    url = (url or "").strip().strip("<>")
    if is_short_link(url):
        url = resolve_short_link(url)
    if not is_spotify_url(url):
        raise ValueError("That is not a Spotify link.")

    track_id = parse_spotify_id(url, "track")
    if track_id:
        track = client.track(track_id)
        return _track_title(track), [_row(track)]

    playlist_id = parse_spotify_id(url, "playlist")
    if playlist_id:
        playlist = client.playlist(playlist_id, fields="name")
        rows = _paginate(
            lambda offset: client.playlist_items(playlist_id, limit=100, offset=offset),
            limit=limit,
            # Spotify's 2026 Web API renamed a playlist entry's "track" to
            # "item"; read either so the change cannot empty every playlist.
            unwrap=lambda item: item.get("track") or item.get("item"),
        )
        return playlist.get("name") or "Spotify playlist", rows

    album_id = parse_spotify_id(url, "album")
    if album_id:
        album = client.album(album_id)
        rows = _paginate(
            lambda offset: client.album_tracks(album_id, limit=50, offset=offset),
            limit=limit,
            unwrap=lambda item: item,
        )
        return album.get("name") or "Spotify album", rows

    raise ValueError("That Spotify link is not a track, album or playlist.")


def _paginate(fetch, *, limit: int, unwrap) -> list[dict]:
    """Walk a Spotify page cursor, stopping at ``limit`` or at a short page.

    A short page is the end of the collection; relying on it means one fewer
    round trip than reading ``next`` and asking for an empty page to confirm.
    """
    rows: list[dict] = []
    offset = 0
    while len(rows) < limit:
        page = fetch(offset) or {}
        items = page.get("items") or []
        if not items:
            break
        for item in items:
            track = unwrap(item) or {}
            if track.get("name"):
                rows.append(_row(track))
                if len(rows) >= limit:
                    break
        if len(items) < (page.get("limit") or len(items)):
            break
        offset += len(items)
    return rows


def _row(track: dict) -> dict:
    return {
        "title": _track_title(track),
        "url": None,
        "duration_s": int((track.get("duration_ms") or 0) / 1000),
        "source": "spotify",
    }
