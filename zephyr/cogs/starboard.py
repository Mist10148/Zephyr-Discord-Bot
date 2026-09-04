"""Starboard: promote a message once enough people react to it.

Three decisions are worth reading before the code.

**The listener is `on_raw_reaction_add`, not `on_reaction_add`.** The non-raw
event only fires for messages in discord.py's message cache, so a starboard
built on it would silently ignore every message older than the current process
-- which is most of them, and exactly the messages people go back to star.

**The guards run cheapest-first, and that ordering is the performance design.**
This handler runs on *every reaction in every guild the bot is in*. A dictionary
lookup rejects the guilds with no starboard; an emoji comparison rejects almost
everything else; only then does anything touch the database or the REST API.
Reordering these would mean a `fetch_message` -- a rate-limitable REST call --
per reaction across every server.

**Promotion is claimed, not checked.** `starboard.claim` inserts and lets the
unique constraint answer, because reactions are independent gateway events with
no ordering guarantee: a read-then-post races with itself and puts the same
message in the starboard twice.
"""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands, tasks

from zephyr.core.logging import get_logger
from zephyr.db import starboard as repo
from zephyr.utils import embeds
from zephyr.db.starboard import (
    DEFAULT_EMOJI,
    DEFAULT_THRESHOLD,
    MAX_THRESHOLD,
    MIN_THRESHOLD,
)

log = get_logger(__name__)

# Discord caps an embed description at 4096, but a starboard entry is a preview
# and not a copy -- somebody who wants the whole thing follows the jump link.
MAX_PREVIEW_CHARS = 1000


def star_display(count: int, emoji: str) -> str:
    return f"{emoji} **{count}**"


def build_embed(message, *, count: int, emoji: str) -> discord.Embed:
    """The starboard entry for one message.

    A jump link rather than a faithful copy: the starboard is an index, and a
    copy would strip the thread, the replies and the reactions that made the
    message worth starring in the first place.
    """
    embed = embeds.brand(
        (message.content or "")[:MAX_PREVIEW_CHARS] or "*no text*",
        # The message's own time, not the promotion's: a starboard is an index
        # of things people said, and stamping it with "now" would make an
        # eight-month-old post look like it was written this afternoon.
        timestamp=False,
    )
    embed.timestamp = message.created_at
    author = message.author
    embed.set_author(
        name=getattr(author, "display_name", str(author)),
        icon_url=author.display_avatar.url if getattr(author, "display_avatar", None) else None,
    )
    embed.add_field(name="Source", value=f"[Jump to message]({message.jump_url})", inline=False)

    # The first image attachment, if any. Only an image: rendering a link to
    # somebody's PDF as a starboard "preview" is worse than omitting it.
    for attachment in getattr(message, "attachments", None) or []:
        if (attachment.content_type or "").startswith("image/"):
            embed.set_image(url=attachment.url)
            break

    embed.set_footer(
        text=embeds.footer_text(f"{emoji} {count}"), icon_url=embeds.icon_url()
    )
    return embed


class StarboardCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Guild id -> config, for the enabled guilds only. This dictionary is
        # the cheapest guard in the listener, so it holds as little as possible.
        self._cache: dict[str, dict] = {}

    async def cog_load(self):
        self._refresh_loop.start()

    def cog_unload(self):
        self._refresh_loop.cancel()

    @tasks.loop(minutes=10)
    async def _refresh_loop(self):
        try:
            self._cache = await asyncio.to_thread(repo.read_all_configs)
        except Exception:
            # The old cache is kept: a stale threshold beats no starboard at all.
            log.exception("Could not refresh the starboard cache")

    @_refresh_loop.before_loop
    async def _before_refresh_loop(self):
        await self.bot.wait_until_ready()

    async def _reload_cache(self):
        try:
            self._cache = await asyncio.to_thread(repo.read_all_configs)
        except Exception:
            log.exception("Could not refresh the starboard cache after a save")

    # ---- listeners -------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self.handle_reaction(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self.handle_reaction(payload)

    def relevant(self, payload) -> dict | None:
        """The guild's config if this reaction could matter, else None.

        Split out from ``handle_reaction`` so the ordering can be tested
        directly -- the cheap-guard sequence is the performance contract of this
        cog, and a test that had to mock a REST call to observe it would not be
        testing the ordering at all.
        """
        if getattr(payload, "guild_id", None) is None:
            return None
        config = self._cache.get(str(payload.guild_id))
        if not config or not config.get("enabled") or not config.get("channel_id"):
            return None
        if str(payload.emoji) != config["emoji"]:
            return None
        if str(payload.channel_id) in config["ignored_channel_ids"]:
            return None
        # Reacting to a starboard entry must not promote the entry into itself,
        # which would then be starrable again -- a two-message loop.
        if str(payload.channel_id) == str(config["channel_id"]):
            return None
        return config

    async def handle_reaction(self, payload) -> bool:
        config = self.relevant(payload)
        if config is None:
            return False
        try:
            return await self._promote(payload, config)
        except Exception:
            # An unhandled exception in a listener is logged by discord.py and
            # otherwise invisible, and it would recur on every reaction.
            log.exception("Could not process a starboard reaction in %s", payload.guild_id)
            return False

    async def _promote(self, payload, config: dict) -> bool:
        guild = self.bot.get_guild(int(payload.guild_id))
        if guild is None:
            return False
        source = guild.get_channel(int(payload.channel_id))
        board = guild.get_channel(int(config["channel_id"]))
        if source is None or board is None:
            log.warning("A starboard channel is missing in guild %s", payload.guild_id)
            return False

        try:
            message = await source.fetch_message(int(payload.message_id))
        except discord.NotFound:
            # Deleted between the reaction and now. Any existing entry goes too,
            # or the starboard keeps an entry linking to nothing.
            await asyncio.to_thread(
                repo.remove_entry, str(payload.guild_id), str(payload.message_id)
            )
            return False
        except discord.Forbidden:
            log.warning("Cannot read %s in guild %s", payload.channel_id, payload.guild_id)
            return False

        count = await self._count(message, config)
        entry = await asyncio.to_thread(
            repo.get_entry, str(payload.guild_id), str(payload.message_id)
        )

        if count < config["threshold"]:
            if entry:
                # Fell back below the threshold: the entry is withdrawn rather
                # than left at a stale count, because a starboard showing "3 ⭐"
                # under a threshold of 5 is a visible contradiction.
                await self._withdraw(board, entry)
            return False

        if entry is None:
            claimed = await asyncio.to_thread(
                repo.claim,
                guild_id=str(payload.guild_id),
                source_channel_id=str(payload.channel_id),
                source_message_id=str(payload.message_id),
                star_count=count,
            )
            if claimed is None:
                # Somebody else's reaction claimed it first. Re-read rather than
                # returning: their count is a moment older than ours.
                entry = await asyncio.to_thread(
                    repo.get_entry, str(payload.guild_id), str(payload.message_id)
                )
            else:
                entry = claimed

        if entry is None:
            return False
        return await self._publish(board, message, entry, count, config)

    async def _count(self, message, config: dict) -> int:
        """How many people have starred this, by the guild's rules.

        The author's own reaction is excluded unless the guild allows it,
        because a starboard anybody can promote themselves into is not a
        starboard.

        Excluding it costs a reaction-user listing, which is why the raw count
        is not simply trusted: `reaction.count` includes the author, and
        `reaction.me` reports whether *the bot* reacted rather than who did.
        The call is paid only on a reaction that has already passed every cheap
        guard -- the configured emoji, in a configured guild, in a channel that
        is not ignored -- so it happens on actual stars and nothing else.
        """
        for reaction in getattr(message, "reactions", None) or []:
            if str(reaction.emoji) != config["emoji"]:
                continue
            count = int(reaction.count)
            if not config["allow_self_star"]:
                try:
                    async for user in reaction.users(limit=None):
                        if user.id == message.author.id:
                            count -= 1
                            break
                except discord.HTTPException:
                    # Counting the author is a smaller error than refusing to
                    # count at all, so the raw total stands.
                    log.warning(
                        "Could not list reactors for %s; counting the author",
                        message.id, exc_info=True,
                    )
            return max(0, count)
        return 0

    async def _publish(self, board, message, entry: dict, count: int, config: dict) -> bool:
        embed = build_embed(message, count=count, emoji=config["emoji"])
        content = star_display(count, config["emoji"])
        existing_id = entry.get("starboard_message_id")

        if existing_id:
            try:
                posted = await board.fetch_message(int(existing_id))
                await posted.edit(content=content, embed=embed)
            except discord.NotFound:
                # Somebody deleted the starboard entry by hand. Forget it so the
                # next reaction re-promotes rather than editing forever into a
                # message that is gone.
                await asyncio.to_thread(
                    repo.remove_entry, entry["guild_id"], entry["source_message_id"]
                )
                return False
        else:
            posted = await board.send(content=content, embed=embed)
            await asyncio.to_thread(
                repo.attach_message,
                entry["guild_id"],
                entry["source_message_id"],
                str(posted.id),
            )

        await asyncio.to_thread(
            repo.set_count, entry["guild_id"], entry["source_message_id"], count
        )
        return True

    async def _withdraw(self, board, entry: dict) -> None:
        message_id = entry.get("starboard_message_id")
        if message_id:
            try:
                posted = await board.fetch_message(int(message_id))
                await posted.delete()
            except (discord.NotFound, discord.Forbidden):
                # Already gone, or not ours to delete. The row goes either way,
                # or the entry is unreachable and un-retryable.
                pass
        await asyncio.to_thread(
            repo.remove_entry, entry["guild_id"], entry["source_message_id"]
        )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        try:
            await asyncio.to_thread(repo.delete_for_guild, str(guild.id))
        except Exception:
            log.exception("Could not forget the starboard for guild %s", guild.id)
        self._cache.pop(str(guild.id), None)

    # ---- commands --------------------------------------------------------

    @app_commands.command(name="starboard", description="Set up the starboard.")
    @app_commands.describe(
        channel="Where promoted messages go. Leave empty to turn the starboard off.",
        threshold=f"How many reactions promote a message (default {DEFAULT_THRESHOLD}).",
        emoji=f"Which reaction counts (default {DEFAULT_EMOJI}).",
        allow_self_star="Whether starring your own message counts. Off by default.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def starboard(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        threshold: app_commands.Range[int, MIN_THRESHOLD, MAX_THRESHOLD] | None = None,
        emoji: str | None = None,
        allow_self_star: bool | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if channel is None:
            await asyncio.to_thread(
                repo.write_config, str(interaction.guild.id), {"enabled": False}
            )
            await self._reload_cache()
            await interaction.followup.send(
                "✅ The starboard is off. Existing entries are left where they are.",
                ephemeral=True,
            )
            return

        me = interaction.guild.me
        permissions = channel.permissions_for(me)
        if not (permissions.send_messages and permissions.embed_links):
            # Both are checked now rather than at the first promotion: a
            # starboard that can send but not embed posts a bare count with no
            # content, which looks like a bug rather than a permission problem.
            await interaction.followup.send(
                f"❌ I need **Send Messages** and **Embed Links** in {channel.mention}.",
                ephemeral=True,
            )
            return

        values = {"enabled": True, "channel_id": str(channel.id)}
        if threshold is not None:
            values["threshold"] = int(threshold)
        if allow_self_star is not None:
            values["allow_self_star"] = bool(allow_self_star)
        if emoji is not None:
            cleaned = emoji.strip()
            if not cleaned or len(cleaned) > 64:
                await interaction.followup.send("❌ That does not look like an emoji.", ephemeral=True)
                return
            values["emoji"] = cleaned

        stored = await asyncio.to_thread(repo.write_config, str(interaction.guild.id), values)
        await self._reload_cache()
        await interaction.followup.send(
            f"⭐ Messages with {stored['threshold']} {stored['emoji']} reactions will be posted "
            f"in {channel.mention}."
            + ("" if stored["allow_self_star"] else "\nStarring your own message does not count."),
            ephemeral=True,
        )

    @app_commands.command(
        name="starboard-ignore", description="Stop the starboard from reading a channel."
    )
    @app_commands.describe(channel="The channel to ignore, or un-ignore if already ignored")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def starboard_ignore(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        await interaction.response.defer(ephemeral=True)
        config = await asyncio.to_thread(repo.read_config, str(interaction.guild.id))
        ignored = list((config or {}).get("ignored_channel_ids") or [])

        if str(channel.id) in ignored:
            ignored.remove(str(channel.id))
            verb = "will be read again"
        else:
            ignored.append(str(channel.id))
            verb = "will be ignored"

        await asyncio.to_thread(
            repo.write_config, str(interaction.guild.id), {"ignored_channel_ids": ignored}
        )
        await self._reload_cache()
        await interaction.followup.send(f"✅ {channel.mention} {verb}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(StarboardCog(bot))
