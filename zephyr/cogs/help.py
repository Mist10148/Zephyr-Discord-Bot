"""Help commands: /helpchat, /helpmusic, /help.

Ported 1:1 from the original bot.py (lines 2295-2449).
"""

import discord
from discord import app_commands
from discord.ext import commands

from zephyr.utils.pagination import _send_paginated_help, _send_paginated_embeds


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="helpchat", description="Show chat-related commands.")
    async def helpchat(self, interaction: discord.Interaction):
        pages = [
            "**Chat Commands:**\n"
            "/prompt - Ask Zephyr a question\n"
            "/settings - Customize AI model and response format\n"
            "/output - Quickly switch between embed and text replies\n"
            "/token - Show Gemini usage stats\n"
            "/generate - Generate an image\n"
            "/image-gen - Generate an image with Gemini\n"
            "/disconnect - Disconnect from voice\n"
            "/say - Make the bot speak in VC\n"
            "/language - Change TTS language"
        ]
        await _send_paginated_help(interaction, "Help - Chat Commands", pages)

    @app_commands.command(name="helpmusic", description="Show music-related commands.")
    async def helpmusic(self, interaction: discord.Interaction):
        pages = [
            ("▶️ Playback", [
                ("/play <query>", "Play a song from YouTube or Spotify"),
                ("/playskip <query>", "Add a song and skip straight to it"),
                ("/playnext <query>", "Add a song to the top of the queue"),
                ("/msearch <query>", "Search YouTube and pick a result"),
                ("/now  or  /np", "Show the currently playing song"),
                ("/pause", "Pause playback"),
                ("/resume", "Resume playback"),
                ("/stop", "Stop and clear the queue"),
                ("/disconnect", "Disconnect from voice"),
                ("/seek <time>", "Jump to a timestamp"),
                ("/forward <time>", "Skip forward"),
                ("/rewind <time>", "Rewind"),
            ]),
            ("📋 Queue", [
                ("/queue [page]", "Show the song queue"),
                ("/skip", "Vote to skip the current song"),
                ("/jump <index>", "Jump to a track in the queue"),
                ("/move <from> <to>", "Move a track in the queue"),
                ("/remove <index> [count]", "Remove track(s) from the queue"),
                ("/clear", "Clear the queue (keeps current song)"),
                ("/shuffle", "Shuffle the queue"),
                ("/loop [mode]", "Cycle or set loop: off / track / queue"),
                ("/loopqueue", "Toggle queue loop"),
            ]),
            ("🔊 Audio, Effects & Voice", [
                ("/join", "Join your voice channel"),
                ("/summon [channel]", "Summon the bot to a channel"),
                ("/leave", "Leave the voice channel"),
                ("/volume <0-1000>", "Set the player volume"),
                ("/bassboost <dB>", "Boost or cut bass"),
                ("/nightcore", "Toggle nightcore mode"),
                ("/vaporwave", "Toggle vaporwave mode"),
                ("/slowed", "Toggle slowed effect"),
                ("/reverb", "Toggle reverb effect"),
                ("/slownrev", "Toggle slowed + reverb"),
                ("/pitch <0.5-2.0>", "Adjust pitch"),
                ("/16d", "Toggle 16D audio effect"),
                ("/reset_effects", "Reset all effects"),
                ("/247", "Toggle 24/7 mode"),
                ("/lyrics [query]", "Show lyrics for the current song"),
            ]),
        ]

        embeds = []
        for title, commands in pages:
            embed = discord.Embed(title=f"🎵 Music Help — {title}", color=discord.Color.blurple())
            for name, value in commands:
                embed.add_field(name=name, value=value, inline=False)
            embeds.append(embed)
        await _send_paginated_embeds(interaction, embeds)

    @app_commands.command(name="help", description="Show all available commands.")
    async def help_command(self, interaction: discord.Interaction):
        pages = [
            ("🌦️ Weather", [
                ("/weather <city>", "Current weather & air quality"),
                ("/forecast <city>", "3-day forecast"),
                ("/temperature <city>", "Current temperature"),
                ("/description <city>", "Weather description"),
                ("/humidity <city>", "Humidity"),
                ("/pressure <city>", "Pressure"),
                ("/windspeed <city>", "Wind speed"),
                ("/air <city>", "Air quality"),
                ("/precipitation <city>", "Precipitation"),
                ("/typhoon", "Typhoon alert for Iloilo"),
                ("/search <city>", "Search weather info"),
                ("/use", "Link to web app"),
                ("/helpweather", "Weather command help"),
            ]),
            ("🎵 Music", [
                ("/play /playskip /playnext", "Play music"),
                ("/msearch", "Search YouTube"),
                ("/now  /np", "Now playing"),
                ("/pause /resume /stop", "Playback control"),
                ("/seek /forward /rewind", "Seek controls"),
                ("/queue /skip /jump /move", "Queue controls"),
                ("/remove /clear /shuffle", "Queue editing"),
                ("/loop /loopqueue", "Loop modes"),
                ("/volume /bassboost /pitch", "Audio settings"),
                ("/nightcore /vaporwave /slowed /reverb /slownrev /16d", "Effects"),
                ("/reset_effects /247", "Reset effects & 24/7"),
                ("/join /summon /leave /disconnect", "Voice connection"),
                ("/lyrics", "Song lyrics"),
                ("/helpmusic", "This music help"),
            ]),
            ("💬 Chat & TTS", [
                ("/prompt", "Ask Zephyr a question"),
                ("/settings", "Customize AI model & format"),
                ("/output", "Switch embed/text replies"),
                ("/token", "Gemini usage stats"),
                ("/generate", "Generate an image"),
                ("/image-gen", "Generate an image with Gemini"),
                ("/say", "Make the bot speak in VC"),
                ("/language", "Change TTS language"),
                ("/disconnect", "Disconnect from voice"),
                ("/helpchat", "Chat command help"),
            ]),
        ]

        embeds = []
        for title, commands in pages:
            embed = discord.Embed(title=f"📖 Command Help — {title}", color=discord.Color.green())
            for name, value in commands:
                embed.add_field(name=name, value=value, inline=False)
            embeds.append(embed)
        await _send_paginated_embeds(interaction, embeds)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
