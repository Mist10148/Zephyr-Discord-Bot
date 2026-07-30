"""Tests for multi-format music input handling.

These tests mock yt-dlp and spotipy so they do not perform any network calls.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

import pytest

from zephyr.cogs.music import (
    YTDLSource,
    YTDLError,
    _sanitize_search,
    _is_url,
    _is_spotify_url,
    _is_spotify_playlist_input,
    _is_youtube_url,
    _is_youtube_playlist,
    _is_audio_file_url,
    _parse_spotify_id,
)


def _make_ctx():
    ctx = MagicMock()
    ctx.author = MagicMock()
    ctx.author.mention = "<@123>"
    ctx.channel = MagicMock()
    return ctx


def _make_info(url, title, vid):
    return {
        "url": url,
        "webpage_url": f"https://example.com/watch?v={vid}",
        "title": title,
        "uploader": "TestUploader",
        "duration": 180,
        "id": vid,
    }


class TestInputHelpers:
    def test_sanitize_search(self):
        assert _sanitize_search("  <hello>  ") == "hello"
        assert _sanitize_search("plain text") == "plain text"

    def test_is_url(self):
        assert _is_url("https://youtube.com/watch?v=abc") is True
        assert _is_url("http://example.com") is True
        assert _is_url("plain text") is False

    def test_is_spotify_url(self):
        assert _is_spotify_url("https://open.spotify.com/track/abc") is True
        assert _is_spotify_url("spotify:track:abc") is True
        assert _is_spotify_url("https://spotify.link/abc") is True
        assert _is_spotify_url("plain text") is False

    def test_is_youtube_url(self):
        assert _is_youtube_url("https://www.youtube.com/watch?v=abc") is True
        assert _is_youtube_url("https://youtu.be/abc") is True
        assert _is_youtube_url("https://music.youtube.com/watch?v=abc") is True
        assert _is_youtube_url("https://soundcloud.com/artist/track") is False
        assert _is_youtube_url("plain text") is False

    def test_is_youtube_playlist(self):
        assert _is_youtube_playlist("https://www.youtube.com/playlist?list=PLabc") is True
        assert _is_youtube_playlist("https://www.youtube.com/watch?v=abc&list=PLabc") is True
        assert _is_youtube_playlist("https://www.youtube.com/watch?v=abc") is False
        assert _is_youtube_playlist("plain text") is False

    def test_is_audio_file_url(self):
        assert _is_audio_file_url("https://example.com/song.mp3") is True
        assert _is_audio_file_url("https://example.com/song.wav") is True
        assert _is_audio_file_url("https://example.com/song.flac") is True
        assert _is_audio_file_url("https://example.com/song.txt") is False
        assert _is_audio_file_url("plain text") is False


class TestSpotifyIdParsing:
    """These are the tests that were impossible before the helpers moved to module
    scope, and their absence is exactly why /play crashed on every Spotify link."""

    def test_parses_web_urls(self):
        assert _parse_spotify_id("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT", "track") == "4cOdK2wGLETKBW3PvgPWqT"
        assert _parse_spotify_id("https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3", "album") == "1DFixLWuPkv3KT3TnV35m3"
        assert _parse_spotify_id("https://open.spotify.com/playlist/37i9dQZF1DX", "playlist") == "37i9dQZF1DX"

    def test_strips_query_strings(self):
        assert _parse_spotify_id("https://open.spotify.com/track/abc?si=xyz", "track") == "abc"

    def test_parses_uris(self):
        assert _parse_spotify_id("spotify:track:abc123", "track") == "abc123"
        assert _parse_spotify_id("spotify:playlist:def456", "playlist") == "def456"
        assert _parse_spotify_id("spotify:track:abc?si=x", "track") == "abc"

    def test_returns_none_for_the_wrong_kind(self):
        assert _parse_spotify_id("spotify:track:abc", "playlist") is None
        assert _parse_spotify_id("https://open.spotify.com/track/abc", "album") is None

    def test_returns_none_for_a_malformed_uri(self):
        assert _parse_spotify_id("spotify:track", "track") is None
        assert _parse_spotify_id("spotify:", "track") is None

    def test_is_spotify_playlist_input(self):
        """A Spotify link is 'playlist input' when it expands to many tracks."""
        assert _is_spotify_playlist_input("https://open.spotify.com/album/abc") is True
        assert _is_spotify_playlist_input("https://open.spotify.com/playlist/abc") is True
        assert _is_spotify_playlist_input("spotify:album:abc") is True
        assert _is_spotify_playlist_input("https://open.spotify.com/track/abc") is False
        assert _is_spotify_playlist_input("spotify:track:abc") is False

    def test_is_spotify_playlist_input_ignores_non_spotify(self):
        assert _is_spotify_playlist_input("https://www.youtube.com/playlist?list=PLabc") is False
        assert _is_spotify_playlist_input("plain text") is False

    def test_tolerates_discord_markdown_brackets(self):
        """Users paste <https://...> to suppress embeds; _play_core sanitises first."""
        assert _is_spotify_playlist_input("<https://open.spotify.com/album/abc>") is True
        assert _is_spotify_playlist_input("<https://open.spotify.com/track/abc>") is False

    def test_the_cog_aliases_still_resolve(self):
        """~4 call sites still say self._parse_spotify_id."""
        from zephyr.cogs.music import MusicCog

        assert MusicCog._parse_spotify_id("spotify:track:abc", "track") == "abc"
        assert callable(MusicCog._resolve_spotify_short_link)


class TestCreateSource:
    @pytest.fixture(autouse=True)
    def patch_audio_source(self):
        # Bypass the real AudioSource type check and FFmpeg init so tests stay
        # fast and do not require a real ffmpeg binary or network stream.
        with patch("zephyr.cogs.music.discord.FFmpegPCMAudio") as mock_ffmpeg, \
             patch("discord.player.PCMVolumeTransformer.__init__", return_value=None), \
             patch("discord.player.AudioSource.__del__", lambda self: None):
            mock_ffmpeg.return_value = MagicMock()
            yield

    @pytest.fixture
    def mock_ytdl(self):
        with patch.object(YTDLSource, "ytdl", new_callable=MagicMock) as m:
            yield m

    @pytest.mark.asyncio
    async def test_plain_text_uses_ytsearch_prefix(self, mock_ytdl):
        mock_ytdl.extract_info.side_effect = [
            {
                "entries": [
                    {"id": "abc123", "title": "Starboy"},
                ]
            },
            _make_info("http://stream.example.com/abc", "Starboy", "abc123"),
        ]

        ctx = _make_ctx()
        result = await YTDLSource.create_source(ctx, "starboy", loop=asyncio.get_event_loop())

        assert isinstance(result, YTDLSource)
        assert result.title == "Starboy"
        # First call should be the prefixed search.
        first_call = mock_ytdl.extract_info.call_args_list[0]
        assert first_call[0][0] == "ytsearch10:starboy"

    @pytest.mark.asyncio
    async def test_youtube_video_url(self, mock_ytdl):
        url = "https://www.youtube.com/watch?v=abc123"
        mock_ytdl.extract_info.side_effect = [
            {"url": url, "webpage_url": url, "extractor_key": "Youtube"},
            _make_info("http://stream.example.com/abc", "Test Song", "abc123"),
        ]

        ctx = _make_ctx()
        result = await YTDLSource.create_source(ctx, url, loop=asyncio.get_event_loop())

        assert isinstance(result, YTDLSource)
        assert result.title == "Test Song"

    @pytest.mark.asyncio
    async def test_youtube_playlist_url_skips_bad_entry(self, mock_ytdl):
        url = "https://www.youtube.com/playlist?list=PLabc"
        mock_ytdl.extract_info.side_effect = [
            {
                "entries": [
                    {"id": "good1"},
                    {"id": "bad1"},
                    {"id": "good2"},
                ]
            },
            _make_info("http://stream.example.com/good1", "Good Song 1", "good1"),
            Exception("unavailable"),
            _make_info("http://stream.example.com/good2", "Good Song 2", "good2"),
        ]

        ctx = _make_ctx()
        results = await YTDLSource.create_source(ctx, url, loop=asyncio.get_event_loop())

        assert isinstance(results, list)
        assert len(results) == 2
        assert results[0].title == "Good Song 1"
        assert results[1].title == "Good Song 2"

    @pytest.mark.asyncio
    async def test_audio_file_url(self, mock_ytdl):
        url = "https://example.com/song.mp3"
        mock_ytdl.extract_info.side_effect = [
            {"url": url, "webpage_url": url, "extractor_key": "Generic"},
            _make_info("http://stream.example.com/song", "Direct Song", "song"),
        ]

        ctx = _make_ctx()
        result = await YTDLSource.create_source(ctx, url, loop=asyncio.get_event_loop())

        assert isinstance(result, YTDLSource)
        assert result.title == "Direct Song"

    @pytest.mark.asyncio
    async def test_no_results_raises_ytdl_error(self, mock_ytdl):
        mock_ytdl.extract_info.return_value = {"entries": []}

        ctx = _make_ctx()
        with pytest.raises(YTDLError):
            await YTDLSource.create_source(ctx, "unknown gibberish query", loop=asyncio.get_event_loop())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
