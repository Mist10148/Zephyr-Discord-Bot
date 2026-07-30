"""Lazy track resolution and the autoplay radio.

The two things Phase 4 added to playback itself.  Both are testable without a
voice connection: resolution is a yt-dlp call away from being pure, and the
radio only reads the current track and appends to the queue.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zephyr.cogs.music import (
    AUTOPLAY_ADD,
    Track,
    VoiceState,
    YTDLError,
    YTDLSource,
    _video_id,
)


def _info(stream_url, title, video_id, duration=100):
    return {
        "url": stream_url,
        "title": title,
        "id": video_id,
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "duration": duration,
        "uploader": "Uploader",
    }


class TestVideoId:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        ],
    )
    def test_the_same_video_is_recognised_through_different_urls(self, url):
        """Autoplay keys its history on this, so tracking parameters must not
        make one video look like several."""
        assert _video_id(url) == "dQw4w9WgXcQ"

    @pytest.mark.parametrize("url", [None, "", "https://example.com/song.mp3", "not a url"])
    def test_anything_else_is_none(self, url):
        assert _video_id(url) is None


class TestLazyResolution:
    """A track imported from Spotify has a title and no URL until it plays."""

    @pytest.fixture
    def mock_ytdl(self):
        with patch.object(YTDLSource, "ytdl", new_callable=MagicMock) as ytdl:
            yield ytdl

    @pytest.fixture
    def no_audio(self):
        with patch("zephyr.cogs.music.discord.FFmpegPCMAudio") as ffmpeg, patch(
            "discord.player.PCMVolumeTransformer.__init__", return_value=None
        ), patch("discord.player.AudioSource.__del__", lambda self: None):
            ffmpeg.return_value = MagicMock()
            yield ffmpeg

    @pytest.mark.asyncio
    async def test_a_track_with_only_a_title_is_searched_for(self, mock_ytdl, no_audio):
        track = Track(title="Artist - Song", source="spotify")
        mock_ytdl.extract_info.side_effect = [
            {"entries": [{"id": "found1", "title": "Artist - Song"}]},
            _info("http://stream/found1", "Artist - Song", "found1", duration=180),
        ]

        await YTDLSource.from_track(track, loop=asyncio.get_event_loop())

        # The search is the *first* call: there is no URL to try and fail on.
        assert mock_ytdl.extract_info.call_args_list[0][0][0] == "ytsearch1:Artist - Song"
        assert "found1" in track.url
        assert track.duration_seconds == 180

    @pytest.mark.asyncio
    async def test_resolution_heals_the_track_so_it_is_paid_for_once(self, mock_ytdl, no_audio):
        track = Track(title="Artist - Song")
        mock_ytdl.extract_info.side_effect = [
            {"entries": [{"id": "found1", "title": "Artist - Song"}]},
            _info("http://stream/found1", "Artist - Song", "found1"),
            _info("http://stream/found1", "Artist - Song", "found1"),
        ]

        await YTDLSource.from_track(track, loop=asyncio.get_event_loop())
        resolved_url = track.url
        await YTDLSource.from_track(track, loop=asyncio.get_event_loop())

        # Second play went straight at the URL: three calls, not four.
        assert mock_ytdl.extract_info.call_count == 3
        assert mock_ytdl.extract_info.call_args_list[2][0][0] == resolved_url

    @pytest.mark.asyncio
    async def test_an_import_that_was_never_playable_is_not_labelled_repaired(self, mock_ytdl, no_audio):
        """repaired_from drives a "playing a re-resolved match" footer, which
        would be a lie for a track that never had a URL to begin with."""
        track = Track(title="Artist - Song")
        mock_ytdl.extract_info.side_effect = [
            {"entries": [{"id": "found1", "title": "Artist - Song"}]},
            _info("http://stream/found1", "Artist - Song", "found1"),
        ]

        await YTDLSource.from_track(track, loop=asyncio.get_event_loop())

        assert track.repaired_from is None

    @pytest.mark.asyncio
    async def test_a_track_with_neither_a_link_nor_a_title_is_refused(self, mock_ytdl, no_audio):
        with pytest.raises(YTDLError, match="neither a link nor a title"):
            await YTDLSource.from_track(Track(title=""), loop=asyncio.get_event_loop())
        assert mock_ytdl.extract_info.call_count == 0

    @pytest.mark.asyncio
    async def test_an_unfindable_title_raises(self, mock_ytdl, no_audio):
        mock_ytdl.extract_info.return_value = {"entries": []}
        with pytest.raises(YTDLError, match="not available"):
            await YTDLSource.from_track(Track(title="Nothing At All"), loop=asyncio.get_event_loop())


class TestAutoplayRadio:
    def _state(self):
        state = VoiceState(MagicMock(), 1, channel_id=2)
        state.bot.user.id = 7
        state.current = Track(title="Seed", url="https://www.youtube.com/watch?v=seedvideo11")
        return state

    @pytest.mark.asyncio
    async def test_the_mix_is_seeded_from_the_current_track(self):
        state = self._state()
        with patch.object(YTDLSource, "resolve_tracks", AsyncMock(return_value=[])) as resolve:
            await state._extend_with_radio()

        assert "list=RDseedvideo11" in resolve.call_args[0][0]

    @pytest.mark.asyncio
    async def test_recently_played_tracks_are_skipped(self):
        """A Mix always leads with its seed, so without this autoplay would put
        the song that just finished straight back on."""
        state = self._state()
        state._remember(state.current)
        candidates = [
            Track(title="Seed", url="https://www.youtube.com/watch?v=seedvideo11"),
            Track(title="Fresh", url="https://www.youtube.com/watch?v=freshvideo1"),
        ]
        with patch.object(YTDLSource, "resolve_tracks", AsyncMock(return_value=candidates)):
            added = await state._extend_with_radio()

        assert added == 1
        assert [track.title for track in state.songs] == ["Fresh"]
        assert list(state.songs)[0].source == "autoplay"

    @pytest.mark.asyncio
    async def test_a_refill_is_capped(self):
        state = self._state()
        candidates = [
            Track(title=f"T{index}", url=f"https://www.youtube.com/watch?v=vid{index:08d}")
            for index in range(AUTOPLAY_ADD + 10)
        ]
        with patch.object(YTDLSource, "resolve_tracks", AsyncMock(return_value=candidates)):
            assert await state._extend_with_radio() == AUTOPLAY_ADD

    @pytest.mark.asyncio
    async def test_a_failing_radio_is_survivable(self):
        """Autoplay is a convenience; a yt-dlp failure must not kill the player."""
        state = self._state()
        with patch.object(YTDLSource, "resolve_tracks", AsyncMock(side_effect=Exception("429"))):
            assert await state._extend_with_radio() == 0

    @pytest.mark.asyncio
    async def test_a_non_youtube_current_track_yields_no_radio(self):
        state = self._state()
        state.current = Track(title="Local", url="https://example.com/song.mp3", source="file")
        with patch.object(YTDLSource, "resolve_tracks", AsyncMock()) as resolve:
            assert await state._extend_with_radio() == 0
        resolve.assert_not_called()
