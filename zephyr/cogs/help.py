"""Help commands: /helpchat, /helpmusic, /help, /helpweather.

All help pages are rendered from the centralized registry in
``zephyr.utils.help_data`` so categories, ordering, and descriptions stay
consistent across every help command.
"""

import discord
from discord import app_commands
from discord.ext import commands

from zephyr.utils.help_data import (
    HELP_CATEGORIES,
    _send_categorized_help,
    categories_by_key,
)


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Show all available commands.")
    async def help_command(self, interaction: discord.Interaction):
        await _send_categorized_help(
            interaction,
            HELP_CATEGORIES,
            title="Command Help",
            color=discord.Color.green(),
            include_toc=True,
        )

    @app_commands.command(name="helpmusic", description="Show music-related commands.")
    async def helpmusic(self, interaction: discord.Interaction):
        await _send_categorized_help(
            interaction,
            categories_by_key(
                "music_playback",
                "music_queue",
                "music_effects",
                "music_voice",
            ),
            title="Music Help",
            color=discord.Color.blurple(),
        )

    @app_commands.command(name="helpchat", description="Show chat and TTS commands.")
    async def helpchat(self, interaction: discord.Interaction):
        await _send_categorized_help(
            interaction,
            categories_by_key("chat", "tts"),
            title="Chat Help",
            color=discord.Color.gold(),
        )

    @app_commands.command(name="helpweather", description="Show weather-related commands.")
    async def helpweather(self, interaction: discord.Interaction):
        await _send_categorized_help(
            interaction,
            categories_by_key("weather"),
            title="Weather Help",
            color=discord.Color.blue(),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
