"""The Zephyr bot: client subclass, cog loading, slash sync, and events.

Combines the original bot setup (lines 69-71), the ``on_message`` handler
(3269-3299), and the ``on_guild_join`` / ``on_ready`` events (3313-3348). Cogs
are loaded once in ``setup_hook`` (the modern, robust equivalent of the
original's load-in-``on_ready``); the same startup console messages are kept.
"""

import asyncio
import math
import time

import discord
from discord.ext import commands, tasks

from zephyr.config import ENABLED_COGS, REDIS_URL
from zephyr.core.opus_loader import load_opus
from zephyr.core.ffmpeg import FFMPEG_PATH
from zephyr.services import bridge
from zephyr.services.bridge import write_guild_snapshot
from zephyr.services.gemini import generate_gemini_response, send_response
from zephyr.services.storage import storage
from zephyr.core.logging import get_logger


log = get_logger(__name__)
# Cog extensions to load (every command lives in one of these).  The names come
# from config so the web tier can report the same list without importing this
# module -- importing it would drag in the storage singleton.
EXTENSIONS = [f"zephyr.cogs.{name}" for name in ENABLED_COGS]


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
        self._started_at = time.time()
        self._command_stream = None

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
                log.exception("Failed to load extension %s", ext)

        # Register slash commands with Discord
        try:
            synced = await self.tree.sync()
            self._synced_count = len(synced)
        except Exception as e:
            log.exception("Failed to sync the command tree")

        if REDIS_URL:
            self._presence_loop.start()
            self._command_loop.start()

    async def close(self):
        """Dispose persistent storage before Discord tears down the loop."""
        self._presence_loop.cancel()
        self._command_loop.cancel()
        self._close_command_stream()
        # Drop the heartbeat rather than letting it age out: a clean shutdown
        # should show as offline immediately, not up to 30 seconds later.
        if REDIS_URL:
            try:
                await asyncio.to_thread(bridge.write_presence, {"online": False})
            except Exception as e:
                log.exception("Failed to publish the shutdown heartbeat")
        storage.close()
        await super().close()

    # ------------------------------------------------------------------
    # Web bridge
    # ------------------------------------------------------------------

    @tasks.loop(seconds=10)
    async def _presence_loop(self):
        """Heartbeat.  The key's 30s TTL is what makes silence mean 'offline'.

        Published three times per TTL so one missed tick -- a slow Redis, a
        blocked loop -- does not read as an outage.
        """
        try:
            await asyncio.to_thread(
                bridge.write_presence,
                {
                    "online": True,
                    "guild_count": len(self.guilds),
                    # discord.py reports NaN until the first heartbeat lands.
                    "latency_ms": None if math.isnan(self.latency) else round(self.latency * 1000),
                    "uptime_s": int(time.time() - self._started_at),
                    "shard": self.shard_id,
                },
            )
        except Exception as e:
            log.exception("Failed to publish presence")

    @_presence_loop.before_loop
    async def _before_presence_loop(self):
        await self.wait_until_ready()

    @tasks.loop(seconds=0.1)
    async def _command_loop(self):
        """Serve the web's commands.

        redis-py is synchronous, so the read happens in a worker thread -- the
        same discipline _publish_guilds uses.  The first read blocks for up to a
        second so latency stays well inside the bridge's 5s budget without
        polling in a tight loop; the rest of the batch is then drained without
        blocking, so a burst of commands is not served one per second.

        Dispatch runs back on the event loop, so no handler ever has to think
        about thread safety.
        """
        try:
            commands_batch = await asyncio.to_thread(self._read_commands)
        except Exception as e:
            # Almost always a dropped connection.  Discard the stream so the next
            # tick resubscribes rather than retrying a dead socket forever.
            log.exception("Bridge listener failed; reopening the stream")
            self._close_command_stream()
            await asyncio.sleep(5)
            return
        for command in commands_batch:
            await self._dispatch_command(command)

    @_command_loop.before_loop
    async def _before_command_loop(self):
        await self.wait_until_ready()

    def _read_commands(self, limit: int = 25) -> list[dict]:
        """Blocking read, in a worker thread.  Returns whatever is pending."""
        if self._command_stream is None:
            self._command_stream = bridge.open_command_stream()
        batch = []
        first = bridge.next_command(self._command_stream, timeout=1.0)
        while first is not None:
            batch.append(first)
            if len(batch) >= limit:
                break
            first = bridge.next_command(self._command_stream, timeout=0)
        return batch

    def _close_command_stream(self):
        stream, self._command_stream = self._command_stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    def _bridge_actions(self) -> dict:
        """Every action any loaded cog serves, plus the bot's own.

        Collected per dispatch rather than cached, so a reloaded cog is picked up
        without a restart -- the cost is a dictionary build per command, which is
        nothing next to the round trip that produced it.
        """
        actions = {"meta.guild": self._bridge_guild_meta, "meta.members": self._bridge_member_names}
        for cog in self.cogs.values():
            provider = getattr(cog, "bridge_actions", None)
            if callable(provider):
                try:
                    actions.update(provider())
                except Exception as e:
                    log.exception("Could not collect bridge actions from %s", cog.__class__.__name__)
        return actions

    async def _dispatch_command(self, command: dict):
        """Run one command and answer on its private response channel.

        Every failure is answered, including the unexpected ones: a caller that
        gets no reply waits out the full 5s timeout and then reports the bot as
        offline, which is a much worse diagnosis than the actual error.
        """
        command_id = command["id"]
        try:
            handler = self._bridge_actions().get(command["action"])
            if handler is None:
                raise LookupError(f"Unknown action {command['action']!r}")
            guild = self.get_guild(int(command["guild_id"])) if command.get("guild_id") else None
            data = await handler(guild, command.get("actor_id"), command.get("args") or {})
            await asyncio.to_thread(bridge.publish_response, command_id, ok=True, data=data or {})
        except Exception as e:
            try:
                await asyncio.to_thread(
                    bridge.publish_response, command_id, ok=False, error=str(e) or e.__class__.__name__
                )
            except Exception as publish_error:
                log.exception("Could not answer bridge command %s", command_id)

    async def _bridge_guild_meta(self, guild, actor_id, args):
        """Text channels and roles, for the dashboard's pickers.

        The web tier has no gateway connection and stores no Discord token, so
        this is the only way it can name a channel.  Only what a picker needs is
        returned -- this is not a general-purpose guild dump.
        """
        if guild is None:
            raise LookupError("Zephyr is not in that server.")
        me = guild.me
        return {
            "channels": [
                {
                    "id": str(channel.id),
                    "name": channel.name,
                    # The picker must not offer a channel the bot cannot post in;
                    # a subscription pointed at one would fail silently forever.
                    "can_send": bool(me and channel.permissions_for(me).send_messages),
                }
                for channel in guild.text_channels
            ],
            "roles": [
                {"id": str(role.id), "name": role.name, "managed": role.managed}
                for role in guild.roles
                if not role.is_default()
            ],
            "voice_channels": [
                {"id": str(channel.id), "name": channel.name} for channel in guild.voice_channels
            ],
        }

    async def _bridge_member_names(self, guild, actor_id, args):
        """Display names for a bounded set of user ids.

        For the audit log, which stores an ``actor_id`` and nothing else -- so
        every row read "Changed by 403285930202595340". Deliberately *not* folded
        into ``meta.guild``: that is read whenever a settings page opens, and a
        guild's whole member list is unbounded, while the audit page holds at
        most a page of rows and therefore a small set of distinct actors.

        Names come from the cache first and only then from the API, and a lookup
        that fails is simply omitted rather than raising -- the caller falls back
        to the raw id, which is worse than a name and much better than an error
        page. The cap is a hard limit because this is reachable from the web.
        """
        if guild is None:
            raise LookupError("Zephyr is not in that server.")

        ids = [str(value) for value in (args.get("ids") or [])][:50]
        members = {}
        for raw_id in ids:
            try:
                user_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except Exception:
                    # Left the guild, or never was in it. The id stands.
                    continue
            members[raw_id] = {
                "id": raw_id,
                "name": member.display_name,
                "avatar_url": member.display_avatar.url if member.display_avatar else None,
            }
        return {"members": list(members.values())}

    async def _publish_guilds(self):
        """Publish the guild list for the web dashboard.

        Off the event loop via to_thread because redis-py is synchronous; a
        blocking call in a coroutine is the bug 625c4ba already fixed once for
        settings persistence.  A snapshot failure must never break the bot, so
        every error is logged and swallowed.
        """
        if not REDIS_URL:
            return
        try:
            snapshot = [
                {"id": str(guild.id), "name": guild.name, "icon": guild.icon.key if guild.icon else None}
                for guild in self.guilds
            ]
            await asyncio.to_thread(write_guild_snapshot, snapshot)
        except Exception as e:
            log.exception("Failed to publish the guild snapshot")

    async def on_ready(self):
        await type_print(f"{self.user} has connected to Discord!")
        await type_print(f"🔹 Synced {self._synced_count} slash command(s)")
        await type_print(f"🔹 Total prefix commands: {len(self.commands)}")
        activity = discord.Activity(type=discord.ActivityType.listening, name="/help")
        await self.change_presence(status=discord.Status.online, activity=activity)
        await self._publish_guilds()

    async def on_guild_remove(self, guild):
        await self._publish_guilds()

    async def on_guild_join(self, guild):
        await self._publish_guilds()
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
            response = await generate_gemini_response(server_id, user_id, final_message, image_url, channel_id=message.channel.id)
            await send_response(message.channel, response, message)

        await self.process_commands(message)


# Module-level instance used by run_bot.py
bot = ZephyrBot()
