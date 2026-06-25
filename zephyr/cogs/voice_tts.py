"""Voice / TTS general commands: /disconnect, /say, /language.

Ported 1:1 from the original bot.py (lines 2218-2264). The module-level
``LANGUAGE`` global becomes the ``tts_language`` attribute on the cog.
"""

import os

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
        await interaction.response.defer()
        tts = gTTS(text=text, lang=self.tts_language)
        tts.save("tts.mp3")
        vc = interaction.guild.voice_client
        if vc and not vc.is_playing():
            audio_source = discord.FFmpegPCMAudio("tts.mp3", executable=FFMPEG_PATH)
            vc.play(audio_source, after=lambda e: os.remove("tts.mp3"))
            await interaction.followup.send(f"Speaking: {text}", ephemeral=True)
        else:
            os.remove("tts.mp3")
            await interaction.followup.send("Bot is already speaking. Wait for it to finish.", ephemeral=True)

    @app_commands.command(name="language", description="Change the TTS language output.")
    @app_commands.describe(lang="Language code (e.g., 'en' for English, 'ja' for Japanese)")
    async def language_command(self, interaction: discord.Interaction, lang: str):
        self.tts_language = lang
        await interaction.response.send_message(f"Language changed to `{lang}`!", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TTSCog(bot))
