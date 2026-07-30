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

# Matches the plan's cap on playlist size, which yt-dlp's max_entries also uses.
MAX_IMPORT_TRACKS = 200


def is_spotify_url(value: str) -> bool:
    candidate = (value or "").strip().strip("<>").lower()
    if candidate.startswith("spotify:"):
        return True
    return ("spotify.com" in candidate or "spotify.link" in candidate) and candidate.startswith(
        ("http://", "https://")
    )


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

    try:
        response = requests.head(url, allow_redirects=True, timeout=10)
        resolved = str(response.url)
        if "spotify.com" in resolved or resolved.startswith("spotify:"):
            return resolved
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
    if "spotify.link" in url:
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
            unwrap=lambda item: item.get("track"),
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
