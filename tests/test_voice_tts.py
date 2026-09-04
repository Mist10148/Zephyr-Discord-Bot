"""Per-guild TTS language.

Nothing in tests/ imported voice_tts before this file, so the cog had no
regression net at all -- which is exactly why the cross-guild bug survived: the
language lived on the cog instance, a cog is a singleton, and one /language call
changed the voice for every server the bot was in.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zephyr.cogs.voice_tts import DEFAULT_LANGUAGE, TTSCog, supported_languages
from zephyr.db import session
from zephyr.db.guild_settings import read_guild_settings, read_tts_languages, write_guild_settings


@pytest.fixture
def db(db_url, monkeypatch):
    """The cog calls the repo with no database_url, so redirect the module name.

    zephyr/db/session.py binds DATABASE_URL at import, which is why this patches
    the module attribute rather than the config -- same as
    tests/test_ai_memory_reset.py.
    """
    monkeypatch.setattr(session, "DATABASE_URL", db_url)
    return db_url


def _cog():
    cog = TTSCog(MagicMock())
    return cog


def _interaction(guild_id=7, user_id=900000000000000001):
    interaction = MagicMock()
    interaction.guild = MagicMock() if guild_id else None
    if guild_id:
        interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


class TestTheLanguageIsPerGuild:
    @pytest.mark.asyncio
    async def test_two_servers_hold_two_languages_at_once(self, db):
        """The whole point. While this lived on the cog instance, the second
        write silently changed the first server's voice too."""
        write_guild_settings("7", {"tts_language": "ja"}, database_url=db)
        write_guild_settings("8", {"tts_language": "fil"}, database_url=db)

        cog = _cog()
        await cog.reload_languages()

        assert cog.language_for(7) == "ja"
        assert cog.language_for(8) == "fil"

    @pytest.mark.asyncio
    async def test_a_guild_that_never_set_one_gets_the_default(self, db):
        cog = _cog()
        await cog.reload_languages()
        assert cog.language_for(999) == DEFAULT_LANGUAGE

    def test_the_bulk_read_omits_guilds_with_no_override(self, db):
        """Absent rather than present-and-null, so a caller's .get(id, default)
        is the whole fallback."""
        write_guild_settings("7", {"tts_language": "ja"}, database_url=db)
        write_guild_settings("8", {"prefix": "z!"}, database_url=db)

        languages = read_tts_languages(database_url=db)
        assert languages == {"7": "ja"}

    @pytest.mark.asyncio
    async def test_it_survives_a_restart(self, db):
        """It used to be process state, so a restart lost it silently."""
        write_guild_settings("7", {"tts_language": "ja"}, database_url=db)
        fresh = _cog()
        await fresh.reload_languages()
        assert fresh.language_for(7) == "ja"

    @pytest.mark.asyncio
    async def test_a_failed_read_keeps_the_previous_cache(self, monkeypatch, caplog):
        """An exception in the loop body would stop it refreshing at all."""
        cog = _cog()
        cog._languages = {"7": "ja"}
        monkeypatch.setattr(
            "zephyr.cogs.voice_tts.read_tts_languages",
            MagicMock(side_effect=RuntimeError("database is down")),
        )
        with caplog.at_level("ERROR", logger="zephyr.cogs.voice_tts"):
            await cog.reload_languages()

        assert cog.language_for(7) == "ja"
        assert "Could not read TTS languages" in caplog.text


class TestTheLanguageCommand:
    @pytest.mark.asyncio
    async def test_it_saves_and_takes_effect_immediately(self, db):
        cog = _cog()
        interaction = _interaction()
        await cog.language_command.callback(cog, interaction, "ja")

        assert read_guild_settings("7", database_url=db)["tts_language"] == "ja"
        # Updated directly rather than waiting for the 10-minute refresh, so the
        # very next /say already speaks in the new language.
        assert cog.language_for(7) == "ja"

    @pytest.mark.asyncio
    async def test_a_bad_code_is_refused_at_the_command(self, db):
        """It took a bare `str` and assigned it unchecked, so a typo surfaced
        later as "TTS failed: Language not supported" from inside /say --
        pointing at the wrong command entirely."""
        cog = _cog()
        interaction = _interaction()
        await cog.language_command.callback(cog, interaction, "klingon")

        sent = interaction.response.send_message.await_args.args[0]
        assert "not a language I can speak" in sent
        assert read_guild_settings("7", database_url=db) is None

    @pytest.mark.asyncio
    async def test_a_code_is_normalised(self, db):
        cog = _cog()
        await cog.language_command.callback(cog, _interaction(), "  JA  ")
        assert read_guild_settings("7", database_url=db)["tts_language"] == "ja"

    @pytest.mark.asyncio
    async def test_a_write_failure_says_so_and_does_not_update_the_cache(self, monkeypatch):
        cog = _cog()
        monkeypatch.setattr(
            "zephyr.cogs.voice_tts.write_guild_settings",
            MagicMock(side_effect=RuntimeError("database is down")),
        )
        interaction = _interaction()
        await cog.language_command.callback(cog, interaction, "ja")

        assert "could not save" in interaction.response.send_message.await_args.args[0].lower()
        assert cog.language_for(7) == DEFAULT_LANGUAGE


class TestAutocomplete:
    @pytest.mark.asyncio
    async def test_it_respects_discords_twenty_five_choice_limit(self):
        cog = _cog()
        choices = await cog._language_autocomplete(_interaction(), "")
        assert 0 < len(choices) <= 25

    @pytest.mark.asyncio
    async def test_it_matches_on_the_name_as_well_as_the_code(self):
        cog = _cog()
        by_name = await cog._language_autocomplete(_interaction(), "japanese")
        assert "ja" in {choice.value for choice in by_name}

        by_code = await cog._language_autocomplete(_interaction(), "ja")
        assert "ja" in {choice.value for choice in by_code}


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_it_tears_the_music_state_down_rather_than_popping_by_hand(self):
        """It used to call state.stop() then pop the dict itself, leaving the
        dashboard snapshot behind -- so the web player kept showing a track
        after a disconnect."""
        cog = _cog()
        music = MagicMock()
        music.voice_states = {7: MagicMock()}
        music.teardown_voice_state = AsyncMock()
        cog.bot.get_cog.return_value = music

        interaction = _interaction()
        interaction.guild.voice_client = MagicMock()
        await cog.disconnect.callback(cog, interaction)

        music.teardown_voice_state.assert_awaited_once_with(7)

    @pytest.mark.asyncio
    async def test_it_still_disconnects_when_music_holds_no_state(self):
        cog = _cog()
        music = MagicMock()
        music.voice_states = {}
        cog.bot.get_cog.return_value = music

        interaction = _interaction()
        interaction.guild.voice_client = MagicMock()
        interaction.guild.voice_client.disconnect = AsyncMock()
        await cog.disconnect.callback(cog, interaction)

        interaction.guild.voice_client.disconnect.assert_awaited_once()


def test_the_language_list_is_real():
    """If gTTS ever stops exposing this, validation silently turns off -- so
    assert it is actually populated rather than an empty fallback."""
    languages = supported_languages()
    assert "en" in languages
    assert len(languages) > 20
