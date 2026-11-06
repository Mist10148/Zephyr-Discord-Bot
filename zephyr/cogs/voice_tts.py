"""Voice / TTS general commands: /disconnect, /say, /language.

Ported 1:1 from the original bot.py (lines 2218-2264). The module-level
``LANGUAGE`` global becomes the ``tts_language`` attribute on the cog.
"""

import asyncio
import os
import tempfile

import discord
from discord import app_commands
from discord.ext import commands
from gtts import gTTS

from zephyr.core.ffmpeg import FFMPEG_PATH


class TTSCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tts_language = "en"  # original module-level LANGUAGE

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
            state = music_cog.voice_states[interaction.guild.id]
            await state.stop()
            music_cog.voice_states.pop(interaction.guild.id, None)
        else:
            await vc.disconnect()
        await interaction.response.send_message("Disconnected!", ephemeral=True)

    @app_commands.command(name="say", description="Make the bot say something in voice chat.")
    @app_commands.describe(text="Text you want the bot to say")
    async def say(self, interaction: discord.Interaction, text: str):
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message("I'm not in a voice call, let me in.", ephemeral=True)
            return
        if self._music_is_active(interaction.guild.id):
            await interaction.response.send_message(
                "Music is playing (or paused) here. Stop it with `/stop` before using `/say`.", ephemeral=True)
            return
        await interaction.response.defer()

        # Write the temporary TTS file to the system temp directory so the bot
        # works on read-only / ephemeral cloud filesystems.
        tts_fd, tts_path = tempfile.mkstemp(suffix=".mp3")
        os.close(tts_fd)
        try:
            tts = gTTS(text=text, lang=self.tts_language)
            # gTTS.save() is a blocking HTTP request; on the event loop it froze
            # every other command (and the gateway heartbeat) while it ran.
            await asyncio.to_thread(tts.save, tts_path)
            vc = interaction.guild.voice_client
            # is_paused() too: play() on a paused client replaces the music's
            # AudioPlayer, orphaning its thread and FFmpeg process and leaving
            # the music loop waiting forever for an after-callback.
            if vc and not vc.is_playing() and not vc.is_paused() and not self._music_is_active(interaction.guild.id):
                audio_source = discord.FFmpegPCMAudio(tts_path, executable=FFMPEG_PATH)
                vc.play(audio_source, after=lambda e: _safe_remove(tts_path))
                await interaction.followup.send(f"Speaking: {text}", ephemeral=True)
            else:
                _safe_remove(tts_path)
                await interaction.followup.send("Bot is already speaking. Wait for it to finish.", ephemeral=True)
        except Exception as exc:
            _safe_remove(tts_path)
            await interaction.followup.send(f"TTS failed: {exc}", ephemeral=True)

    def _music_is_active(self, guild_id: int) -> bool:
        """Whether the music player owns the voice client right now.

        Between tracks the client is briefly idle while the next one loads; a
        /say in that gap made the music's voice.play() fail with "Already
        playing audio", so a current track or a non-empty queue counts too.
        """
        music_cog = self.bot.get_cog('MusicCog')
        state = music_cog.peek_voice_state(guild_id) if music_cog else None
        return bool(state and (state.current is not None or len(state.songs)))

    @app_commands.command(name="language", description="Change the TTS language output.")
    @app_commands.describe(lang="Language code (e.g., 'en' for English, 'ja' for Japanese)")
    async def language_command(self, interaction: discord.Interaction, lang: str):
        self.tts_language = lang
        await interaction.response.send_message(f"Language changed to `{lang}`!", ephemeral=True)


def _safe_remove(path: str):
    try:
        os.remove(path)
    except Exception:
        pass


async def setup(bot: commands.Bot):
    await bot.add_cog(TTSCog(bot))
