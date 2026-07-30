"""Tests for multi-format music input handling.

These tests mock yt-dlp and spotipy so they do not perform any network calls.
"""

import asyncio
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


def _make_info(url, title, vid, duration=180):
    """A full (process=True) extraction result, as from_track receives at play time."""
    return {
        "url": url,
        "webpage_url": f"https://example.com/watch?v={vid}",
        "title": title,
        "uploader": "TestUploader",
        "duration": duration,
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


class TestResolveTracks:
    # There is deliberately no audio-source fixture here any more.
    #
    # The old one patched discord.FFmpegPCMAudio, PCMVolumeTransformer.__init__ and
    # AudioSource.__del__, and it existed only because resolution used to construct
    # audio sources. That is precisely what hid the leak: FFmpegPCMAudio.__init__
    # spawns a subprocess immediately, so a 200-track playlist built 200 of them and
    # the mock made that invisible. Resolution now touches no audio machinery at all,
    # so the absence of those patches is itself the assertion --
    # test_resolution_spawns_no_ffmpeg_process makes it explicit.

    @pytest.fixture
    def mock_ytdl(self):
        with patch.object(YTDLSource, "ytdl", new_callable=MagicMock) as m:
            yield m

    @pytest.mark.asyncio
    async def test_plain_text_uses_ytsearch_prefix(self, mock_ytdl):
        mock_ytdl.extract_info.return_value = {
            "entries": [{"id": "abc123", "title": "Starboy", "duration": 230}]
        }

        tracks = await YTDLSource.resolve_tracks("starboy", loop=asyncio.get_event_loop())

        assert len(tracks) == 1
        assert tracks[0].title == "Starboy"
        assert tracks[0].duration_seconds == 230
        # The prefixed search is still how plain text is routed.
        assert mock_ytdl.extract_info.call_args_list[0][0][0] == "ytsearch10:starboy"

    @pytest.mark.asyncio
    async def test_plain_text_takes_only_the_top_hit(self, mock_ytdl):
        """A search must enqueue one track, not all ten results."""
        mock_ytdl.extract_info.return_value = {
            "entries": [{"id": f"v{i}", "title": f"T{i}"} for i in range(10)]
        }
        tracks = await YTDLSource.resolve_tracks("starboy", loop=asyncio.get_event_loop())
        assert len(tracks) == 1
        assert tracks[0].title == "T0"

    @pytest.mark.asyncio
    async def test_youtube_video_url(self, mock_ytdl):
        url = "https://www.youtube.com/watch?v=abc123"
        mock_ytdl.extract_info.return_value = {
            "url": url, "webpage_url": url, "title": "Test Song", "duration": 180,
        }

        tracks = await YTDLSource.resolve_tracks(url, loop=asyncio.get_event_loop())

        assert len(tracks) == 1
        assert tracks[0].title == "Test Song"
        assert tracks[0].url == url

    @pytest.mark.asyncio
    async def test_playlist_resolves_in_one_call(self, mock_ytdl):
        """Was 1 + N extractions; a flat entry already carries id/title/duration."""
        url = "https://www.youtube.com/playlist?list=PLabc"
        mock_ytdl.extract_info.return_value = {
            "entries": [
                {"id": "good1", "title": "Good Song 1", "duration": 100},
                {"id": "good2", "title": "Good Song 2", "duration": 200},
            ]
        }

        tracks = await YTDLSource.resolve_tracks(url, loop=asyncio.get_event_loop())

        assert [t.title for t in tracks] == ["Good Song 1", "Good Song 2"]
        assert mock_ytdl.extract_info.call_count == 1

    @pytest.mark.asyncio
    async def test_playlist_drops_entries_with_no_usable_url(self, mock_ytdl):
        """Resolve-time filtering is now only about missing URLs; dead links are a
        play-time concern, which is where the URL is actually used."""
        mock_ytdl.extract_info.return_value = {
            "entries": [{"id": "good1", "title": "Keep"}, {"title": "No id or url"}]
        }
        tracks = await YTDLSource.resolve_tracks(
            "https://www.youtube.com/playlist?list=PLabc", loop=asyncio.get_event_loop())
        assert [t.title for t in tracks] == ["Keep"]

    @pytest.mark.asyncio
    async def test_playlist_respects_max_entries(self, mock_ytdl):
        mock_ytdl.extract_info.return_value = {
            "entries": [{"id": f"v{i}", "title": f"T{i}"} for i in range(50)]
        }
        tracks = await YTDLSource.resolve_tracks(
            "https://www.youtube.com/playlist?list=PLabc", loop=asyncio.get_event_loop(), max_entries=10)
        assert len(tracks) == 10

    @pytest.mark.asyncio
    async def test_resolution_spawns_no_ffmpeg_process(self, mock_ytdl):
        """The leak regression test.

        discord.FFmpegPCMAudio.__init__ Popens immediately, so building one per queue
        entry leaked a process per entry -- and the player re-created the source at
        play time anyway, so every one of them was wasted.
        """
        mock_ytdl.extract_info.return_value = {
            "entries": [{"id": f"v{i}", "title": f"T{i}", "duration": 100} for i in range(50)]
        }

        with patch("zephyr.cogs.music.discord.FFmpegPCMAudio") as ffmpeg:
            tracks = await YTDLSource.resolve_tracks(
                "https://www.youtube.com/playlist?list=PLabc", loop=asyncio.get_event_loop())

        assert len(tracks) == 50
        assert ffmpeg.call_count == 0
        assert mock_ytdl.extract_info.call_count == 1

    @pytest.mark.asyncio
    async def test_search_spawns_no_ffmpeg_process(self, mock_ytdl):
        """/msearch used to build ten sources and discard nine of them."""
        mock_ytdl.extract_info.return_value = {
            "entries": [{"id": f"v{i}", "title": f"T{i}"} for i in range(10)]
        }

        with patch("zephyr.cogs.music.discord.FFmpegPCMAudio") as ffmpeg:
            tracks = await YTDLSource.search_tracks("starboy", loop=asyncio.get_event_loop())

        assert len(tracks) == 10
        assert ffmpeg.call_count == 0
        assert mock_ytdl.extract_info.call_count == 1

    @pytest.mark.asyncio
    async def test_audio_file_url_skips_ytdl_entirely(self, mock_ytdl):
        """_is_audio_file_url had no caller before; FFmpeg can read the URL itself."""
        url = "https://example.com/song.mp3"
        tracks = await YTDLSource.resolve_tracks(url, loop=asyncio.get_event_loop())

        assert len(tracks) == 1
        assert tracks[0].source == "file"
        assert tracks[0].url == url
        assert tracks[0].title == "song.mp3"
        assert mock_ytdl.extract_info.call_count == 0

    @pytest.mark.asyncio
    async def test_no_results_raises_ytdl_error(self, mock_ytdl):
        mock_ytdl.extract_info.return_value = {"entries": []}

        with pytest.raises(YTDLError):
            await YTDLSource.resolve_tracks("unknown gibberish query", loop=asyncio.get_event_loop())

    @pytest.mark.asyncio
    async def test_none_from_ytdl_raises_ytdl_error(self, mock_ytdl):
        mock_ytdl.extract_info.return_value = None

        with pytest.raises(YTDLError):
            await YTDLSource.resolve_tracks("anything", loop=asyncio.get_event_loop())

    @pytest.mark.asyncio
    async def test_requester_is_carried_onto_every_track(self, mock_ytdl):
        """Stored by id and mention, because a Member is not serializable."""
        mock_ytdl.extract_info.return_value = {
            "entries": [{"id": f"v{i}", "title": f"T{i}"} for i in range(3)]
        }
        tracks = await YTDLSource.resolve_tracks(
            "https://www.youtube.com/playlist?list=PLabc",
            requester_id=42, requester_mention="<@42>", loop=asyncio.get_event_loop())
        assert all(t.requester_id == 42 and t.requester_mention == "<@42>" for t in tracks)


class TestFromTrack:
    """from_track is the only place an FFmpeg source is constructed."""

    @pytest.fixture
    def mock_ytdl(self):
        with patch.object(YTDLSource, "ytdl", new_callable=MagicMock) as m:
            yield m

    @pytest.fixture
    def no_audio(self):
        """Only from_track needs the audio machinery stubbed -- resolution does not."""
        with patch("zephyr.cogs.music.discord.FFmpegPCMAudio") as ffmpeg, \
             patch("discord.player.PCMVolumeTransformer.__init__", return_value=None), \
             patch("discord.player.AudioSource.__del__", lambda self: None):
            ffmpeg.return_value = MagicMock()
            yield ffmpeg

    def _track(self, **kwargs):
        from zephyr.cogs.music import Track

        defaults = dict(title="Song", url="https://example.com/watch?v=1", duration_seconds=100)
        return Track(**{**defaults, **kwargs})

    @pytest.mark.asyncio
    async def test_builds_one_source_and_backfills_metadata(self, mock_ytdl, no_audio):
        track = self._track(title="Placeholder", duration_seconds=0)
        mock_ytdl.extract_info.return_value = _make_info("http://stream/1", "Real Title", "1", duration=240)

        await YTDLSource.from_track(track, loop=asyncio.get_event_loop())

        assert no_audio.call_count == 1
        assert track.title == "Real Title"
        assert track.duration_seconds == 240

    @pytest.mark.asyncio
    async def test_a_seek_is_passed_to_ffmpeg(self, mock_ytdl, no_audio):
        mock_ytdl.extract_info.return_value = _make_info("http://stream/1", "Song", "1")
        await YTDLSource.from_track(self._track(), seek=42, loop=asyncio.get_event_loop())
        assert "-ss 42" in no_audio.call_args.kwargs["before_options"]

    @pytest.mark.asyncio
    async def test_a_direct_file_never_touches_ytdl(self, mock_ytdl, no_audio):
        track = self._track(url="https://example.com/song.mp3", source="file")
        await YTDLSource.from_track(track, loop=asyncio.get_event_loop())
        assert mock_ytdl.extract_info.call_count == 0
        assert no_audio.call_args.args[0] == "https://example.com/song.mp3"

    @pytest.mark.asyncio
    async def test_a_dead_url_is_re_resolved_by_title(self, mock_ytdl, no_audio):
        """The re-resolve path: stored playlist URLs rot as videos are removed."""
        track = self._track(title="Findable Song", url="https://example.com/watch?v=dead")
        mock_ytdl.extract_info.side_effect = [
            Exception("Video unavailable"),
            {"entries": [{"id": "new1", "title": "Findable Song"}]},
            _make_info("http://stream/new1", "Findable Song", "new1"),
        ]

        await YTDLSource.from_track(track, loop=asyncio.get_event_loop())

        assert track.repaired_from == "https://example.com/watch?v=dead"
        assert "new1" in track.url
        assert mock_ytdl.extract_info.call_args_list[1][0][0] == "ytsearch1:Findable Song"

    @pytest.mark.asyncio
    async def test_a_timeout_is_not_re_resolved(self, mock_ytdl, no_audio):
        """A network stall will stall a search too -- retrying doubles the wait."""
        mock_ytdl.extract_info.side_effect = asyncio.TimeoutError()
        with pytest.raises(asyncio.TimeoutError):
            await YTDLSource.from_track(self._track(), loop=asyncio.get_event_loop())
        assert mock_ytdl.extract_info.call_count == 1

    @pytest.mark.asyncio
    async def test_an_unfindable_dead_url_raises(self, mock_ytdl, no_audio):
        mock_ytdl.extract_info.side_effect = [
            Exception("Video unavailable"),
            {"entries": []},
        ]
        with pytest.raises(YTDLError):
            await YTDLSource.from_track(self._track(), loop=asyncio.get_event_loop())

    @pytest.mark.asyncio
    async def test_no_title_to_search_with_raises_immediately(self, mock_ytdl, no_audio):
        url = "https://example.com/watch?v=dead"
        mock_ytdl.extract_info.side_effect = Exception("Video unavailable")
        with pytest.raises(YTDLError):
            await YTDLSource.from_track(self._track(title=url, url=url), loop=asyncio.get_event_loop())
        assert mock_ytdl.extract_info.call_count == 1


class TestTrack:
    def _make_track(self, **kwargs):
        from zephyr.cogs.music import Track

        defaults = dict(title="Song", url="https://example.com/watch?v=1", duration_seconds=213,
                        requester_id=1, requester_mention="<@1>", uploader="Uploader")
        return Track(**{**defaults, **kwargs})

    def test_duration_is_formatted_from_seconds(self):
        assert self._make_track(duration_seconds=213).duration == "3 minutes, 33 seconds"
        assert self._make_track(duration_seconds=0).duration == "Unknown"

    def test_str_is_the_queue_label(self):
        assert str(self._make_track()) == "**Song** by **Uploader**"

    def test_absorb_backfills_placeholders_without_clobbering(self):
        """Flat entries lack thumbnails and uploader URLs until play time."""
        track = self._make_track(title="Placeholder", uploader="Unknown", thumbnail=None)
        track.absorb({"title": "Real Title", "uploader": "Real Uploader",
                      "thumbnail": "http://img", "duration": 300})
        assert track.title == "Real Title"
        assert track.uploader == "Real Uploader"
        assert track.thumbnail == "http://img"
        assert track.duration_seconds == 300

    def test_absorb_keeps_existing_values_when_the_info_is_empty(self):
        track = self._make_track()
        track.absorb({})
        assert track.title == "Song"
        assert track.duration_seconds == 213

    def test_progress_bar_moves_and_stays_the_right_width(self):
        from zephyr.cogs.music import Track

        start = Track._progress_bar(0, 100)
        middle = Track._progress_bar(50, 100)
        end = Track._progress_bar(100, 100)
        assert start.index("🔘") == 0
        assert 0 < middle.index("🔘") < end.index("🔘")
        assert all(len(bar) == 15 for bar in (start, middle, end))

    def test_progress_bar_handles_a_zero_duration(self):
        from zephyr.cogs.music import Track

        assert "🔘" in Track._progress_bar(0, 0)

    def test_embed_includes_progress_only_when_asked(self):
        assert not any(f.name == "Progress" for f in self._make_track().create_embed().fields)
        assert any(f.name == "Progress" for f in self._make_track().create_embed(elapsed=60).fields)

    def test_embed_notes_a_re_resolved_track(self):
        embed = self._make_track(repaired_from="https://example.com/dead").create_embed()
        assert "re-resolved" in (embed.footer.text or "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
