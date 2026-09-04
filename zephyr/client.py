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

from zephyr.config import COMMAND_PREFIX, ENABLED_COGS, REDIS_URL, SHARD_COUNT
from zephyr.core.opus_loader import load_opus
from zephyr.core.errors import report as report_command_error
from zephyr.core.ffmpeg import FFMPEG_PATH
from zephyr.core.streaming import StreamingReply
from zephyr.db.guild_settings import read_ai_channel_policies, read_prefixes
from zephyr.services import bridge
from zephyr.utils import command_registry, embeds
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


class ZephyrBot(commands.AutoShardedBot):
    """The bot.

    AutoShardedBot rather than Bot even at one shard: with SHARD_COUNT unset it
    opens a single connection and behaves identically, so there is no separate
    "sharded" code path to keep working. Discord requires sharding past roughly
    2,500 guilds, and discovering then that the base class has to change is a
    worse time to find out.

    All shards run in this one process. That is deliberate rather than
    incidental: MusicCog.voice_states, the bridge command listener and gemini's
    in-memory conversation buffer are all per-process, and splitting shards
    across processes would need each of them redesigned. What sharding buys here
    is several gateway connections, which is the part Discord actually requires.
    """

    def __init__(self):
        # Enumerated rather than Intents.all(). `all()` requests every
        # privileged intent, including presences and typing, which this bot
        # never reads -- and each one has to be justified to Discord for
        # verification past 100 guilds. What is actually used:
        #
        #   guilds          -- guild/channel/role caches, the snapshot, pickers
        #   members         -- resolving an audit actor and a DJ role holder
        #   message_content -- the AI answers mentions and replies
        #   voice_states    -- the empty-channel listener and the player
        #   messages        -- on_message at all
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        intents.voice_states = True
        intents.guild_messages = True
        intents.dm_messages = True

        super().__init__(
            # None lets Discord pick, which is right until somebody has a
            # reason to pin it.
            shard_count=SHARD_COUNT,
            # Not when_mentioned_or: a mention is already the AI's trigger in
            # on_message, so accepting it as a prefix too would make
            # "@Zephyr weather" both ask the AI and run the weather command.
            command_prefix=self._resolve_prefix,
            intents=intents,
            # None, not DefaultHelpCommand: zephyr/cogs/help.py provides the
            # real help surface, and registering both meant two implementations
            # of /help with one of them unstyled.
            help_command=None,
        )
        self._synced_count = 0
        self._started_at = time.time()
        self._command_stream = None
        # guild_id (str) -> prefix. Absent means COMMAND_PREFIX. Cached because
        # command_prefix is consulted for every message the bot can see, so a
        # query there would put the database on the path of *reading a chat
        # message*.
        self._prefixes: dict[str, str] = {}
        # guild_id -> (mode, channel_ids). Absent means the AI answers wherever
        # it can read, which is the historical behaviour.
        self._ai_channel_policies: dict[str, tuple[str, set[str]]] = {}

    def _resolve_prefix(self, bot, message):
        """The prefix for this message's guild, or the deployment default.

        Synchronous by necessity -- discord.py calls this per message and will
        await a coroutine, but doing IO here would be a query per message. The
        cache is refreshed on a loop and updated directly by a /prefix change.
        """
        if message.guild is None:
            return COMMAND_PREFIX
        return self._prefixes.get(str(message.guild.id), COMMAND_PREFIX)

    async def reload_prefixes(self) -> None:
        try:
            self._prefixes = await asyncio.to_thread(read_prefixes)
        except Exception:
            # A stale cache answers with the previous prefix; an exception here
            # would take the loop down and stop it refreshing at all.
            log.exception("Could not read guild prefixes")
        try:
            self._ai_channel_policies = await asyncio.to_thread(read_ai_channel_policies)
        except Exception:
            log.exception("Could not read AI channel policies")

    def ai_may_answer(self, message) -> bool:
        """Whether the AI is allowed to answer in this channel.

        The mention/reply handler answered anywhere the bot could read, so a
        server that wanted Zephyr for music had no way to stop people
        conversing with it in every channel. A guild with no policy still
        answers everywhere -- this adds a restriction, it does not impose one.

        Checked against the *parent* for a thread, because a policy naming
        #bot-spam should cover threads started in it rather than being silently
        bypassed by anyone who opens one.
        """
        if message.guild is None:
            return True
        policy = self._ai_channel_policies.get(str(message.guild.id))
        if policy is None:
            return True
        mode, channel_ids = policy

        candidates = {str(message.channel.id)}
        parent_id = getattr(message.channel, "parent_id", None)
        if parent_id is not None:
            candidates.add(str(parent_id))

        if mode == "allow":
            return bool(candidates & channel_ids)
        return not (candidates & channel_ids)

    @tasks.loop(minutes=10)
    async def _prefix_loop(self):
        await self.reload_prefixes()

    @_prefix_loop.before_loop
    async def _before_prefix_loop(self):
        await self.wait_until_ready()

    async def setup_hook(self):
        # Every slash command's errors, in one place. Assigned rather than
        # decorated: `self.tree` is the default CommandTree that
        # commands.Bot.__init__ built, so there is no module-level tree object
        # to hang a @tree.error decorator on.
        self.tree.on_error = self._on_app_command_error

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

        # Not gated on REDIS_URL, unlike the two below: the prefix is a
        # database read, and the database is always configured.
        self._prefix_loop.start()

        if REDIS_URL:
            self._presence_loop.start()
            self._command_loop.start()

    async def close(self):
        """Dispose persistent storage before Discord tears down the loop."""
        self._prefix_loop.cancel()
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
                    # shard_id is None on an AutoShardedBot: it owns several
                    # rather than one, so the count is the meaningful number
                    # and the dashboard reports that instead.
                    "shard": self.shard_id,
                    "shard_count": self.shard_count,
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

    async def _on_app_command_error(self, interaction: discord.Interaction, error: Exception):
        """Every slash command's last line of defence.

        Before this, an unhandled exception in any of the 75 slash commands
        produced "The application did not respond" and nothing in any log.
        """
        await report_command_error(interaction, error)

    async def on_command_error(self, ctx: commands.Context, error: Exception):
        """The same for the prefix surface.

        Replaces MusicCog.cog_command_error, which sent a red embed containing
        str(error) for any failure -- no logging, and no distinction between a
        cooldown and a crash.
        """
        from zephyr.core.errors import GENERIC, new_reference, user_facing_message
        from zephyr.core.tracking import error_context

        message = user_facing_message(error)
        if message == "":
            return
        if message is None:
            reference = new_reference()
            command = ctx.command.qualified_name if ctx.command else "unknown"
            guild_id = str(ctx.guild.id) if ctx.guild else None
            # Tagged as well as logged, for the reason `errors.report` gives:
            # the reference is only useful if it can be searched for.
            with error_context(
                reference=reference, command=command,
                guild_id=guild_id, user_id=str(ctx.author.id),
            ):
                log.error(
                    "Unhandled error in prefix command %s",
                    command,
                    exc_info=error,
                    extra={
                        "reference": reference,
                        "guild_id": guild_id,
                        "user_id": str(ctx.author.id),
                    },
                )
            message = GENERIC.format(reference=reference)
        try:
            await ctx.send(message)
        except discord.HTTPException:
            log.warning("Could not deliver an error message for %s", ctx.command)

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
        # Here rather than in setup_hook: the bot's own avatar URL does not
        # exist until the gateway hands the user object over, and every embed
        # the factory builds carries it in the footer.
        embeds.configure(
            name=self.user.name if self.user else None,
            icon_url=self.user.display_avatar.url if self.user else None,
        )
        await type_print(f"{self.user} has connected to Discord!")
        await type_print(f"🔹 Synced {self._synced_count} slash command(s)")
        await type_print(f"🔹 Total prefix commands: {len(self.commands)}")
        activity = discord.Activity(type=discord.ActivityType.listening, name="/help")
        await self.change_presence(status=discord.Status.online, activity=activity)
        await self._publish_guilds()
        await self._publish_commands()

    async def _publish_commands(self):
        """Publish the command list the tree actually holds.

        Best effort and after everything else: the bot works without a
        dashboard, and a Redis that is down must not stop it starting. The
        derived list is the fix for three hand-maintained copies of "which
        commands exist" -- during Phase 15 the docs said 75 while the tree held
        114, and nothing noticed.
        """
        if not REDIS_URL:
            return
        try:
            await asyncio.to_thread(bridge.write_commands, command_registry.payload(self.tree))
        except Exception:
            log.warning("Could not publish the command list", exc_info=True)

    async def on_guild_remove(self, guild):
        await self._publish_guilds()

    async def on_guild_join(self, guild):
        """Introduce the bot, once, in the server's own greeting channel.

        The embed said "I am your Weather Bot" and listed five commands, three
        of them weather.  Zephyr has been a music, AI, moderation and reminder
        bot for some time, so the first thing a new server saw was a description
        of a different product.

        The channel choice changed too.  It used to post in the first channel
        the bot could write to, which in a server with an ordered channel list
        is #rules or #announcements -- exactly where an unsolicited bot
        introduction is least welcome.  `system_channel` is the channel the
        server itself nominated for joins and is tried first.
        """
        await self._publish_guilds()
        embed = embeds.brand(
            "Weather, music, AI chat, reminders and moderation. `/help` lists everything.",
            title="Thanks for adding Zephyr 🌦️",
        )
        embed.add_field(name="Weather", value="`/weather` · `/forecast` · `/weather-subscribe`", inline=False)
        embed.add_field(name="Music", value="`/play` · `/queue` · `/dj-only`", inline=False)
        embed.add_field(name="AI", value="`/prompt` · mention Zephyr to chat", inline=False)
        embed.add_field(name="Reminders", value="`/remindme 2h take the bins out`", inline=False)
        embed.add_field(name="Moderation", value="`/warn` · `/timeout` · `/modlog`", inline=False)
        embed.add_field(name="Dashboard", value="`/use` for the web player and settings", inline=False)

        candidates = [guild.system_channel, *guild.text_channels]
        for channel in candidates:
            if channel is None:
                continue
            try:
                if channel.permissions_for(guild.me).send_messages:
                    await channel.send(embed=embed)
                    break
            except discord.HTTPException:
                # Try the next one rather than giving up: a single channel with
                # an odd overwrite must not cost the introduction entirely.
                log.warning("Could not introduce Zephyr in %s", channel.id, exc_info=True)

    async def _read_attachments(self, message):
        """The first image and the first text file on a message.

        Only the *first* attachment was considered before, so a message with a
        caption file and an image saw whichever came first and silently
        discarded the other.
        """
        image_url, text_content = None, None
        for attachment in getattr(message, "attachments", None) or []:
            content_type = attachment.content_type or ""
            if image_url is None and content_type.startswith("image/"):
                image_url = attachment.url
            elif text_content is None and attachment.filename.lower().endswith((".txt", ".md")):
                try:
                    text_content = (await attachment.read()).decode("utf-8", errors="replace")
                except Exception:
                    log.warning("Could not read attachment %s", attachment.filename, exc_info=True)
            if image_url and text_content:
                break
        return image_url, text_content

    async def _read_referenced_attachments(self, message):
        """The same, for the message being replied to.

        ``reference.resolved`` is only populated when Discord happened to
        include it, so the message is fetched when it is not -- once, and only
        on a reply that carried no attachment of its own.
        """
        reference = getattr(message, "reference", None)
        if reference is None:
            return None, None

        referenced = reference.resolved
        if referenced is None or isinstance(referenced, discord.DeletedReferencedMessage):
            if reference.message_id is None:
                return None, None
            try:
                referenced = await message.channel.fetch_message(reference.message_id)
            except Exception:
                # Deleted, or in a channel history the bot cannot read.
                return None, None
        return await self._read_attachments(referenced)

    async def on_message(self, message):
        if message.author == self.user:
            return
        is_reply_to_bot = message.reference and message.reference.resolved and message.reference.resolved.author == self.user
        in_dm = isinstance(message.channel, discord.DMChannel)
        if not (self.user.mentioned_in(message) or is_reply_to_bot or in_dm):
            await self.process_commands(message)
            return
        if not self.ai_may_answer(message):
            # Silently, not with a refusal: a channel configured to keep the AI
            # out should be quiet, and "I am not allowed to talk here" is still
            # the bot talking there.
            await self.process_commands(message)
            return

        async with message.channel.typing():
            server_id = message.guild.id if message.guild else None
            user_id = message.author.id
            image_url, text_content = await self._read_attachments(message)
            if image_url is None:
                # The common flow this used to miss entirely: reply to somebody
                # else's screenshot and ask about it. Only the *replying*
                # message was inspected, so the image was invisible and the
                # answer was about nothing.
                image_url, referenced_text = await self._read_referenced_attachments(message)
                text_content = text_content or referenced_text
            clean_message = message.content.replace(f"<@!{self.user.id}>", "").replace(f"<@{self.user.id}>", "").strip()
            # Both, not either: a .txt used to *replace* whatever was typed, so
            # "summarise this" plus a file lost the instruction.
            final_message = "\n\n".join(part for part in (clean_message, text_content) if part)
            if not final_message and not image_url:
                if not (in_dm and image_url):
                    await message.channel.send("Please provide a message when mentioning or replying to me.")
                await self.process_commands(message)
                return
            # The reply is shown as it arrives, then replaced by the real
            # message. send_response still owns the final formatting -- the
            # three output formats, the chunking and the file fallback all stay
            # in one place, and this only replaces the waiting.
            async with StreamingReply(message.channel) as preview:
                response = await generate_gemini_response(
                    server_id, user_id, final_message, image_url,
                    channel_id=message.channel.id,
                    on_progress=preview.update,
                )
            await send_response(message.channel, response, message)

        await self.process_commands(message)


# Module-level instance used by run_bot.py
bot = ZephyrBot()
