"""The Zephyr bot: client subclass, cog loading, slash sync, and events.

Combines the original bot setup (lines 69-71), the ``on_message`` handler
(3269-3299), and the ``on_guild_join`` / ``on_ready`` events (3313-3348). Cogs
are loaded once in ``setup_hook`` (the modern, robust equivalent of the
original's load-in-``on_ready``); the same startup console messages are kept.
"""

import asyncio

import discord
from discord.ext import commands

from zephyr.core.opus_loader import load_opus
from zephyr.core.ffmpeg import FFMPEG_PATH
from zephyr.services.gemini import generate_gemini_response, send_response

# Cog extensions to load (every command lives in one of these)
EXTENSIONS = [
    "zephyr.cogs.weather",
    "zephyr.cogs.music",
    "zephyr.cogs.voice_tts",
    "zephyr.cogs.chat",
    "zephyr.cogs.help",
]


async def type_print(text, delay=0.03):
    for char in text:
        print(char, end="", flush=True)
        await asyncio.sleep(delay)
    print()
    await asyncio.sleep(1)


class ZephyrBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix="/",
            intents=intents,
            help_command=commands.DefaultHelpCommand(no_category="General"),
        )
        self._synced_count = 0

    async def setup_hook(self):
        # Voice prerequisites
        load_opus()
        print(f"[Startup] Using FFmpeg: {FFMPEG_PATH}")

        # Load every cog
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                print(f"✅ Loaded {ext}")
            except Exception as e:
                print(f"⚠️ Failed to load {ext}: {e}")

        # Register slash commands with Discord
        try:
            synced = await self.tree.sync()
            self._synced_count = len(synced)
        except Exception as e:
            print(f"⚠️ Failed to sync commands: {e}")

    async def on_ready(self):
        await type_print(f"{self.user} has connected to Discord!")
        await type_print(f"🔹 Synced {self._synced_count} slash command(s)")
        await type_print(f"🔹 Total prefix commands: {len(self.commands)}")
        activity = discord.Activity(type=discord.ActivityType.listening, name="/help")
        await self.change_presence(status=discord.Status.online, activity=activity)

    async def on_guild_join(self, guild):
        welcome_embed = discord.Embed(title="Hello! I am your Weather Bot 🌦️", color=discord.Color.gold())
        welcome_embed.description = "Here are some commands you can use:"
        welcome_embed.add_field(name="/weather <city>", value="Current weather", inline=False)
        welcome_embed.add_field(name="/forecast <city>", value="3-day forecast", inline=False)
        welcome_embed.add_field(name="/prompt", value="Ask me anything", inline=False)
        welcome_embed.add_field(name="/play", value="Play music", inline=False)
        welcome_embed.add_field(name="/help", value="See all commands", inline=False)
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                await channel.send(embed=welcome_embed)
                break

    async def on_message(self, message):
        if message.author == self.user:
            return
        is_reply_to_bot = message.reference and message.reference.resolved and message.reference.resolved.author == self.user
        in_dm = isinstance(message.channel, discord.DMChannel)
        if not (self.user.mentioned_in(message) or is_reply_to_bot or in_dm):
            await self.process_commands(message)
            return

        async with message.channel.typing():
            server_id = message.guild.id if message.guild else None
            user_id = message.author.id
            image_url, text_content = None, None
            if message.attachments:
                attachment = message.attachments[0]
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    image_url = attachment.url
                elif attachment.filename.endswith(".txt"):
                    text_content = (await attachment.read()).decode("utf-8")
            clean_message = message.content.replace(f"<@!{self.user.id}>", "").replace(f"<@{self.user.id}>", "").strip()
            final_message = text_content or clean_message
            if not final_message and not image_url:
                if not (in_dm and image_url):
                    await message.channel.send("Please provide a message when mentioning or replying to me.")
                await self.process_commands(message)
                return
            response = await generate_gemini_response(server_id, user_id, final_message, image_url)
            await send_response(message.channel, response, message)

        await self.process_commands(message)


# Module-level instance used by run_bot.py
bot = ZephyrBot()
