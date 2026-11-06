"""/say must never take the voice client away from the music player."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from zephyr.cogs.music import SongQueue, Track
from zephyr.cogs.voice_tts import TTSCog


def _cog(state):
    bot = MagicMock()
    music = MagicMock()
    music.peek_voice_state.return_value = state
    bot.get_cog.return_value = music
    return TTSCog(bot)


def test_a_current_track_counts_as_active_even_while_paused():
    state = SimpleNamespace(current=Track(title="Song"), songs=SongQueue())
    assert _cog(state)._music_is_active(1)


def test_a_queued_track_between_songs_counts_as_active():
    songs = SongQueue()
    songs.put_nowait(Track(title="Next"))
    assert _cog(SimpleNamespace(current=None, songs=songs))._music_is_active(1)


def test_no_music_state_is_free_to_speak():
    assert not _cog(None)._music_is_active(1)
