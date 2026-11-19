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


def _language_interaction(guild_id):
    from unittest.mock import AsyncMock

    interaction = MagicMock()
    interaction.guild.id = guild_id
    interaction.response.send_message = AsyncMock()
    return interaction


def test_the_tts_language_is_per_server():
    import asyncio

    cog = TTSCog(MagicMock())
    asyncio.run(TTSCog.language_command.callback(cog, _language_interaction(1), "ja"))
    assert cog.language_for(1) == "ja"
    assert cog.language_for(2) == "en"


def test_an_unknown_language_is_refused_up_front():
    import asyncio

    cog = TTSCog(MagicMock())
    interaction = _language_interaction(1)
    asyncio.run(TTSCog.language_command.callback(cog, interaction, "klingon"))
    assert cog.language_for(1) == "en"
    assert "not a language" in interaction.response.send_message.await_args.args[0]


def test_a_clip_deletes_its_file_after_ffmpeg_is_cleaned_up(tmp_path, monkeypatch):
    """The after-callback ran before FFmpeg was killed, so Windows kept the file."""
    import discord

    from zephyr.cogs import voice_tts

    clip_file = tmp_path / "clip.mp3"
    clip_file.write_bytes(b"x")
    order = []
    monkeypatch.setattr(discord.FFmpegPCMAudio, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(discord.FFmpegPCMAudio, "cleanup",
                        lambda self: order.append(("ffmpeg", clip_file.exists())))

    clip = voice_tts._TempClip(str(clip_file))
    clip.cleanup()

    assert order == [("ffmpeg", True)]
    assert not clip_file.exists()


def test_say_text_is_bounded():
    from zephyr.cogs.voice_tts import MAX_SAY_LENGTH

    parameter = next(p for p in TTSCog.say.parameters if p.name == "text")
    assert parameter.max_value == MAX_SAY_LENGTH <= 1900
