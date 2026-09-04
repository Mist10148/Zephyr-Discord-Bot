"""Voice / TTS general commands: /disconnect, /say, /language.

The TTS language used to be ``self.tts_language`` on the cog instance. A cog is
a singleton, so one person running ``/language ja`` changed the voice for **every
server the bot was in**, silently, until the next restart -- and because it was
process state it was lost on that restart anyway.

It is per guild now, in guild_settings, read through a cache refreshed on a
loop -- the same shape as ``MusicCog.reload_dj_roles``, because /say reads this
on every invocation and a query per invocation would put the database on the
critical path of speaking a sentence.

Guild-only, with no DM fallback: /say requires ``interaction.guild.voice_client``
and a DM has no voice channel, so there is no DM path to have a language for.
"""

import asyncio
import functools
import os
import tempfile

import discord
from discord import app_commands
from discord.ext import commands, tasks
from gtts import gTTS

from zephyr.core.ffmpeg import FFMPEG_PATH
from zephyr.core.logging import get_logger
from zephyr.db.guild_settings import read_tts_languages, write_guild_settings

log = get_logger(__name__)

DEFAULT_LANGUAGE = "en"
# The cache is only a latency optimisation, so a long interval is fine: a
# /language call updates it directly rather than waiting for the next refresh.
LANGUAGE_REFRESH_MINUTES = 10


@functools.cache
def supported_languages() -> dict[str, str]:
    """gTTS's own language table, code -> name.

    Cached because it is a static dict in the library but the accessor is not
    free, and it is read by both the autocomplete and the validator. Wrapped in
    a try/except because it has reached out to the network in some gTTS
    versions, and a language list that cannot be fetched must not stop /say from
    working with a code the caller already knows is good.
    """
    try:
        from gtts.lang import tts_langs

        return dict(tts_langs())
    except Exception:
        log.exception("Could not read the gTTS language list; validation is disabled")
        return {}


class TTSCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild_id (str) -> language code. Absent means "use the default".
        self._languages: dict[str, str] = {}

    async def cog_load(self):
        self._language_loop.start()

    def cog_unload(self):
        self._language_loop.cancel()

    @tasks.loop(minutes=LANGUAGE_REFRESH_MINUTES)
    async def _language_loop(self):
        await self.reload_languages()

    @_language_loop.before_loop
    async def _before_language_loop(self):
        await self.bot.wait_until_ready()

    async def reload_languages(self) -> None:
        try:
            self._languages = await asyncio.to_thread(read_tts_languages)
        except Exception:
            # A stale cache speaks in the previous language; an exception here
            # would take the loop down and stop it refreshing at all.
            log.exception("Could not read TTS languages")

    def language_for(self, guild_id: int | None) -> str:
        """This guild's language, or the default."""
        if guild_id is None:
            return DEFAULT_LANGUAGE
        return self._languages.get(str(guild_id), DEFAULT_LANGUAGE)

    @app_commands.command(name="disconnect", description="Disconnect the bot from the voice call.")
    async def disconnect(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("I'm not in a voice channel!", ephemeral=True)
            return
        music_cog = self.bot.get_cog('MusicCog')
        if music_cog and interaction.guild.id in music_cog.voice_states:
            # One call rather than stop-then-pop-by-hand: teardown_voice_state
            # also clears the dashboard snapshot, which this used to leave
            # behind so the web player kept showing a track after a disconnect.
            await music_cog.teardown_voice_state(interaction.guild.id)
        else:
            await vc.disconnect()
        await interaction.response.send_message("Disconnected!", ephemeral=True)

    @app_commands.command(name="say", description="Make the bot say something in voice chat.")
    @app_commands.describe(text="Text you want the bot to say")
    async def say(self, interaction: discord.Interaction, text: str):
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message("I'm not in a voice call, let me in.", ephemeral=True)
            return
        await interaction.response.defer()

        language = self.language_for(interaction.guild.id)

        # Write the temporary TTS file to the system temp directory so the bot
        # works on read-only / ephemeral cloud filesystems.
        tts_fd, tts_path = tempfile.mkstemp(suffix=".mp3")
        os.close(tts_fd)
        try:
            tts = gTTS(text=text, lang=language)
            tts.save(tts_path)
            vc = interaction.guild.voice_client
            if vc and not vc.is_playing():
                audio_source = discord.FFmpegPCMAudio(tts_path, executable=FFMPEG_PATH)
                vc.play(audio_source, after=lambda e: _safe_remove(tts_path))
                await interaction.followup.send(f"Speaking: {text}", ephemeral=True)
            else:
                _safe_remove(tts_path)
                await interaction.followup.send("Bot is already speaking. Wait for it to finish.", ephemeral=True)
        except Exception as exc:
            _safe_remove(tts_path)
            log.exception("TTS failed for language %s", language)
            await interaction.followup.send(f"TTS failed: {exc}", ephemeral=True)

    @app_commands.command(name="language", description="Change the TTS language for this server.")
    @app_commands.describe(lang="Language code — start typing to see the options")
    @app_commands.guild_only()
    async def language_command(self, interaction: discord.Interaction, lang: str):
        code = lang.strip().lower()
        languages = supported_languages()
        # Validated at last. This took a bare `str` and assigned it unchecked,
        # so a typo surfaced later as "TTS failed: Language not supported" from
        # inside /say, pointing at the wrong command entirely.
        if languages and code not in languages:
            await interaction.response.send_message(
                f"`{lang}` is not a language I can speak. Try `en`, `ja`, `fil`, `es`…",
                ephemeral=True,
            )
            return

        name = languages.get(code, code)
        try:
            await asyncio.to_thread(
                write_guild_settings, str(interaction.guild.id), {"tts_language": code}
            )
        except Exception:
            log.exception("Could not save the TTS language")
            await interaction.response.send_message(
                "I could not save that — try again shortly.", ephemeral=True
            )
            return
        # Updated directly rather than waiting for the refresh loop, so the very
        # next /say already speaks in the new language.
        self._languages[str(interaction.guild.id)] = code

        await interaction.response.send_message(
            f"TTS language for this server is now **{name}** (`{code}`).", ephemeral=True
        )

    @language_command.autocomplete("lang")
    async def _language_autocomplete(self, interaction: discord.Interaction, current: str):
        """Discord allows 25 choices and 3 seconds; the list is static and cached."""
        term = current.strip().lower()
        matches = [
            app_commands.Choice(name=f"{name} ({code})", value=code)
            for code, name in sorted(supported_languages().items(), key=lambda item: item[1])
            if not term or term in code or term in name.lower()
        ]
        return matches[:25]


def _safe_remove(path: str):
    try:
        os.remove(path)
    except Exception:
        pass


async def setup(bot: commands.Bot):
    await bot.add_cog(TTSCog(bot))
