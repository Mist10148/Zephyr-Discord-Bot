"""Spotify links in the player: metadata only, resolved at play time."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zephyr.cogs import music
from zephyr.cogs.music import MusicCog, YTDLError, YTDLSource
from zephyr.services import spotify


def _cog(sp=object()):
    cog = MusicCog.__new__(MusicCog)
    cog.bot = MagicMock()
    cog.sp = sp
    return cog


@pytest.mark.asyncio
async def test_a_spotify_playlist_becomes_unresolved_tracks_with_no_youtube_lookups(monkeypatch):
    rows = [{"title": "Artist - Song %d" % i, "url": None, "duration_s": 200, "source": "spotify"} for i in range(150)]
    monkeypatch.setattr(music, "fetch_playlist_metadata", lambda client, url: ("Mix", rows))
    resolve = AsyncMock()
    monkeypatch.setattr(YTDLSource, "resolve_tracks", resolve)

    tracks = await _cog()._resolve_query(
        "https://open.spotify.com/playlist/abc", requester_id=7, requester_mention="<@7>")

    assert len(tracks) == 150
    assert all(track.url is None and track.source == "spotify" for track in tracks)
    assert tracks[0].requester_id == 7
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_queries_still_go_to_yt_dlp(monkeypatch):
    resolve = AsyncMock(return_value=["track"])
    monkeypatch.setattr(YTDLSource, "resolve_tracks", resolve)

    assert await _cog()._resolve_query("starboy", requester_id=1, requester_mention="") == ["track"]


@pytest.mark.asyncio
async def test_a_spotify_failure_is_a_readable_error(monkeypatch):
    def boom(client, url):
        raise RuntimeError("http 404")

    monkeypatch.setattr(music, "fetch_playlist_metadata", boom)
    with pytest.raises(YTDLError, match="Spotify could not read that link"):
        await _cog()._resolve_query("https://open.spotify.com/playlist/abc", requester_id=1, requester_mention="")


@pytest.mark.asyncio
async def test_no_spotify_client_is_a_readable_error():
    with pytest.raises(YTDLError, match="not available"):
        await _cog(sp=None)._resolve_query("https://open.spotify.com/track/abc", requester_id=1, requester_mention="")


def test_playlist_entries_under_the_new_item_key_are_read():
    client = MagicMock()
    client.playlist.return_value = {"name": "Mix"}
    client.playlist_items.return_value = {
        "items": [{"item": {"name": "Song", "artists": [{"name": "Artist"}], "duration_ms": 1000}}],
        "limit": 100,
    }

    name, rows = spotify.fetch_playlist_metadata(client, "https://open.spotify.com/playlist/abc")

    assert name == "Mix"
    assert rows == [{"title": "Artist - Song", "url": None, "duration_s": 1, "source": "spotify"}]


def test_only_real_spotify_hosts_count_as_spotify_links():
    from zephyr.services.spotify import is_spotify_url, resolve_short_link

    assert is_spotify_url("https://open.spotify.com/track/abc")
    assert is_spotify_url("https://spotify.link/abc")
    assert is_spotify_url("spotify:track:abc")
    assert not is_spotify_url("https://10.0.0.5:8080/x?spotify.link")
    assert not is_spotify_url("http://127.0.0.1/open.spotify.com/track/1")
    assert not is_spotify_url("https://evilspotify.com/track/1")
    # Never fetched: not a short-link host.
    assert resolve_short_link("https://10.0.0.5/?spotify.link") == "https://10.0.0.5/?spotify.link"


class _Head:
    def __init__(self, location=None):
        self.headers = {"Location": location} if location else {}
        self.is_redirect = location is not None


def test_a_short_link_is_followed_one_spotify_hop_at_a_time(monkeypatch):
    import requests
    from zephyr.services.spotify import resolve_short_link

    hops = {
        "https://spotify.link/abc": _Head("https://spotify.app.link/abc?x=1"),
        "https://spotify.app.link/abc?x=1": _Head("https://open.spotify.com/track/123"),
    }
    seen = []

    def head(url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        seen.append(url)
        return hops[url]

    monkeypatch.setattr(requests, "head", head)
    assert resolve_short_link("https://spotify.link/abc") == "https://open.spotify.com/track/123"
    assert seen == ["https://spotify.link/abc", "https://spotify.app.link/abc?x=1"]


def test_a_short_link_redirecting_elsewhere_is_not_followed(monkeypatch):
    """Each hop used to be fetched before anything checked it."""
    import requests
    from zephyr.services.spotify import resolve_short_link

    seen = []

    def head(url, **kwargs):
        seen.append(url)
        return _Head("http://169.254.169.254/latest/meta-data/")

    monkeypatch.setattr(requests, "head", head)
    assert resolve_short_link("https://spotify.link/abc") == "https://spotify.link/abc"
    assert seen == ["https://spotify.link/abc"]


def test_a_mixed_case_spotify_uri_is_parsed():
    from zephyr.services.spotify import is_spotify_url, parse_spotify_id
    assert is_spotify_url("Spotify:Track:4uLU6hMCjMI75M1A2tKUQC")
    assert parse_spotify_id("Spotify:Track:4uLU6hMCjMI75M1A2tKUQC", "track") == "4uLU6hMCjMI75M1A2tKUQC"


def test_the_client_never_sleeps_off_a_rate_limit(monkeypatch):
    """urllib3 honoured Retry-After for up to six hours per retry."""
    import spotipy
    from zephyr.services.spotify import build_client

    seen = {}
    monkeypatch.setattr(spotipy, "Spotify", lambda **kwargs: seen.update(kwargs) or "client")
    assert build_client("id", "secret") == "client"
    assert seen["retries"] == 0 and seen["status_retries"] == 0
