"""Message activity and leveling.

**Nothing here writes to the database on a message.** That is the single
constraint the whole cog is built around. `on_message` fires for every message
in every guild Zephyr can see, and a write on that path would turn a busy
weekend into a write per message per guild — for a leaderboard nobody reads more
than once an hour. Counts accumulate in `_pending` and a `tasks.loop` hands the
whole batch over.

The accumulator has two safety properties, and both exist because of specific
ways this design fails:

**A size cap forces an early flush.** Without it, `_pending` grows with traffic
between ticks, and the one time that matters is when the database is unreachable
— the batch is kept, the next tick fails too, and the dictionary grows until the
process dies.

**A `@_flush_loop.error` handler restarts the loop.** A raising `tasks.loop` is
cancelled, not retried. Without the handler, one unexpected error stops
flushing silently and `_pending` grows to an out-of-memory failure hours later,
with nothing in the log tying the two together.

Redis is deliberately not involved in v1. It would move the accumulator out of
process and survive a restart, at the cost of a network round trip per message —
which is the thing this design exists to avoid.
"""

import asyncio
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from zephyr.core.logging import get_logger
from zephyr.db import activity as repo
from zephyr.utils import embeds

log = get_logger(__name__)

# How often the accumulator is written out. A minute is far more often than
# anybody reads a leaderboard and rare enough that the write volume is
# proportional to guilds rather than to messages.
FLUSH_MINUTES = 1

# Force a flush at this many pending members, regardless of the clock. See the
# module docstring: this is the bound that keeps an unreachable database from
# becoming an out-of-memory failure.
MAX_PENDING = 5000

# Per-person, per-guild. Without it, XP measures how fast somebody can type
# rather than how much they take part -- and the leaderboard becomes a record of
# who spammed most.
XP_COOLDOWN_SECONDS = 60

# The cooldown map is bounded too. It is keyed per member per guild and would
# otherwise hold an entry for every person who has ever spoken.
MAX_COOLDOWN_ENTRIES = 50_000


def render_bar(into: int, needed: int, width: int = 12) -> str:
    """A text progress bar.

    Text rather than an image: generating one would mean Pillow work on a
    command anybody can run repeatedly, and the number is the information.
    """
    if needed <= 0:
        return "█" * width
    filled = max(0, min(width, round(width * into / needed)))
    return "█" * filled + "░" * (width - filled)


class ActivityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Guild id -> config, enabled guilds only: the cheapest guard.
        self._cache: dict[str, dict] = {}
        # (guild_id, user_id) -> messages counted since the last flush.
        self._pending: dict[tuple[str, str], int] = {}
        # (guild_id, user_id) -> monotonic time of the last XP award.
        self._cooldowns: dict[tuple[str, str], float] = {}
        # (guild_id, user_id) -> the channel they last spoke in, so a level-up
        # can be announced where it was earned. Kept beside _pending rather
        # than inside it so the batch handed to the repository stays a plain
        # count map.
        self._last_channel: dict[tuple[str, str], str] = {}

    async def cog_load(self):
        self._refresh_loop.start()
        self._flush_loop.start()

    async def cog_unload(self):
        self._refresh_loop.cancel()
        self._flush_loop.cancel()
        # Written out on the way down rather than discarded: an orderly
        # shutdown is the one case where losing the batch is avoidable.
        await self._flush()

    # ---- the loops -------------------------------------------------------

    @tasks.loop(minutes=10)
    async def _refresh_loop(self):
        try:
            self._cache = await asyncio.to_thread(repo.read_all_configs)
        except Exception:
            log.exception("Could not refresh the activity cache")

    @_refresh_loop.before_loop
    async def _before_refresh_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=FLUSH_MINUTES)
    async def _flush_loop(self):
        await self._flush()

    @_flush_loop.before_loop
    async def _before_flush_loop(self):
        await self.bot.wait_until_ready()

    @_flush_loop.error
    async def _flush_loop_error(self, error):
        """A raising tasks.loop is cancelled, not retried.

        Without this, one unexpected error stops flushing silently and
        `_pending` grows until the process runs out of memory hours later, with
        nothing in the log connecting the two events.
        """
        log.exception("The activity flush loop stopped unexpectedly", exc_info=error)
        self._flush_loop.restart()

    async def _flush(self) -> int:
        """Hand the accumulator over and clear it.

        Swapped before the write, not after: a message arriving mid-flush must
        land in the new dictionary rather than in the batch being written, or it
        is counted twice.
        """
        if not self._pending:
            return 0
        batch, self._pending = self._pending, {}
        try:
            result = await asyncio.to_thread(repo.flush, batch)
        except Exception:
            # The batch is dropped rather than retried. Merging it back would
            # be the obvious choice and is the wrong one: a database that is
            # down stays down for minutes, and a batch that keeps growing while
            # being retried is exactly the unbounded growth MAX_PENDING exists
            # to prevent. A few minutes of counts is an acceptable loss for a
            # leaderboard.
            log.exception("Could not flush %d activity records", len(batch))
            return 0

        for crossing in result.get("level_ups", []):
            await self._announce(crossing)
        for key in batch:
            self._last_channel.pop(key, None)
        return result.get("touched", 0)

    # ---- the hot path ----------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        self.count(message)

    def count(self, message) -> bool:
        """Record one message, in memory. Returns whether it counted.

        Synchronous and allocation-light on purpose: this runs for every message
        in every guild, and it must not await, must not read the database and
        must not touch the network.
        """
        if getattr(message, "guild", None) is None:
            return False
        author = getattr(message, "author", None)
        if author is None or getattr(author, "bot", False):
            return False

        config = self._cache.get(str(message.guild.id))
        if not config or not config.get("enabled"):
            return False
        if str(message.channel.id) in config["ignored_channel_ids"]:
            return False

        key = (str(message.guild.id), str(author.id))
        now = time.monotonic()
        last = self._cooldowns.get(key)
        if last is not None and now - last < XP_COOLDOWN_SECONDS:
            return False

        if len(self._cooldowns) >= MAX_COOLDOWN_ENTRIES:
            # Cleared wholesale rather than evicted one at a time: an LRU here
            # would be real bookkeeping on the hottest path in the bot, and the
            # cost of clearing is that a few people get one extra XP award.
            self._cooldowns.clear()
        self._cooldowns[key] = now
        self._pending[key] = self._pending.get(key, 0) + 1
        self._last_channel[key] = str(message.channel.id)

        if len(self._pending) >= MAX_PENDING:
            # Fire and forget: this path must not await. The loop keeps running
            # regardless, so a failed forced flush is retried on schedule.
            self.bot.loop.create_task(self._flush())
        return True

    # ---- level-ups -------------------------------------------------------

    async def _announce(self, crossing: dict) -> bool:
        """Say that somebody levelled up, if the guild wants it said.

        Driven from the flush result rather than from the message handler,
        because a level is a function of the *stored* total: announcing from the
        in-memory delta would announce a level the database does not yet agree
        with, and announce it again after a restart.
        """
        config = self._cache.get(crossing["guild_id"])
        if not config or not config.get("announce_level_ups"):
            return False
        guild = self.bot.get_guild(int(crossing["guild_id"]))
        if guild is None:
            return False

        channel_id = config.get("announce_channel_id") or self._last_channel.get(
            (crossing["guild_id"], crossing["user_id"])
        )
        if not channel_id:
            return False
        destination = guild.get_channel(int(channel_id))
        if destination is None:
            return False

        try:
            await destination.send(
                f"🎉 <@{crossing['user_id']}> reached **level {crossing['level']}**!",
                # A level-up names one person. Suppressing the rest costs
                # nothing and means this can never become a mass ping.
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=True
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            log.warning(
                "Could not announce a level-up in guild %s", crossing["guild_id"], exc_info=True
            )
            return False
        return True

    # ---- commands --------------------------------------------------------

    @app_commands.command(name="rank", description="Your level and message count here.")
    @app_commands.describe(member="Whose rank to show. Defaults to you.")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None):
        await interaction.response.defer()
        target = member or interaction.user
        # Flushed first, so /rank does not report a number that is up to a
        # minute stale -- which reads as the bot not counting.
        await self._flush()

        stats = await asyncio.to_thread(repo.get_member, str(interaction.guild.id), str(target.id))
        if stats is None:
            await interaction.followup.send(
                f"{target.display_name} has not spoken here yet."
                if member else "You have not spoken here yet.",
            )
            return
        position = await asyncio.to_thread(
            repo.rank_of, str(interaction.guild.id), str(target.id)
        )

        embed = embeds.info(
            f"{render_bar(stats['xp_into_level'], stats['xp_for_next_level'])} "
            f"{stats['xp_into_level']}/{stats['xp_for_next_level']} XP",
            title=f"Level {stats['level']}",
        )
        embed.set_author(
            name=target.display_name,
            icon_url=target.display_avatar.url if target.display_avatar else None,
        )
        embed.add_field(name="Messages", value=f"{stats['messages']:,}", inline=True)
        embed.add_field(name="Total XP", value=f"{stats['xp']:,}", inline=True)
        if position:
            embed.add_field(name="Rank", value=f"#{position}", inline=True)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="leaderboard", description="The most active members here.")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self._flush()
        rows = await asyncio.to_thread(repo.leaderboard, str(interaction.guild.id), limit=10)
        if not rows:
            await interaction.followup.send("Nobody has any activity recorded here yet.")
            return

        lines = []
        for position, row in enumerate(rows, start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(position, f"**{position}.**")
            lines.append(
                f"{medal} <@{row['user_id']}> — level {row['level']} · "
                f"{row['messages']:,} messages"
            )
        await interaction.followup.send(
            embed=embeds.brand("\n".join(lines), title="Most active"),
            # A leaderboard mentions ten people, and pinging all of them every
            # time somebody runs it would make the command a nuisance.
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="activity", description="Turn activity tracking on or off.")
    @app_commands.describe(
        enabled="Whether to count messages and award levels",
        announce_channel="Where level-ups are announced. Empty means where they were earned.",
        announce_level_ups="Whether to announce level-ups at all",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def activity(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        announce_channel: discord.TextChannel | None = None,
        announce_level_ups: bool | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        values = {"enabled": bool(enabled)}
        if announce_channel is not None:
            values["announce_channel_id"] = str(announce_channel.id)
        if announce_level_ups is not None:
            values["announce_level_ups"] = bool(announce_level_ups)

        await asyncio.to_thread(repo.write_config, str(interaction.guild.id), values)
        await self._reload_cache()

        if not enabled:
            await interaction.followup.send(
                "✅ Activity tracking is off. Existing totals are kept — "
                "turning it back on resumes from where it stopped.",
                ephemeral=True,
            )
            return
        where = (
            announce_channel.mention if announce_channel
            else "the channel the level was earned in"
        )
        await interaction.followup.send(
            f"📈 Counting messages. Level-ups are announced in {where}.", ephemeral=True
        )

    @app_commands.command(
        name="activity-ignore", description="Stop counting messages in a channel."
    )
    @app_commands.describe(channel="The channel to ignore, or un-ignore if already ignored")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def activity_ignore(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        await interaction.response.defer(ephemeral=True)
        config = await asyncio.to_thread(repo.read_config, str(interaction.guild.id))
        ignored = list((config or {}).get("ignored_channel_ids") or [])
        if str(channel.id) in ignored:
            ignored.remove(str(channel.id))
            verb = "counts again"
        else:
            ignored.append(str(channel.id))
            verb = "will not be counted"
        await asyncio.to_thread(
            repo.write_config, str(interaction.guild.id), {"ignored_channel_ids": ignored}
        )
        await self._reload_cache()
        await interaction.followup.send(f"✅ {channel.mention} {verb}.", ephemeral=True)

    @app_commands.command(name="activity-today", description="How busy this server is today.")
    @app_commands.guild_only()
    async def activity_today(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self._flush()
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summary = await asyncio.to_thread(repo.daily_summary, str(interaction.guild.id), day)
        await interaction.followup.send(
            embed=embeds.info(
                f"**{summary['messages']:,}** messages from "
                f"**{summary['active']}** {'person' if summary['active'] == 1 else 'people'}.",
                title="Today",
                footer=f"UTC day {day}",
            )
        )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        try:
            await asyncio.to_thread(repo.delete_for_guild, str(guild.id))
        except Exception:
            log.exception("Could not forget activity for guild %s", guild.id)
        self._cache.pop(str(guild.id), None)

    async def _reload_cache(self):
        try:
            self._cache = await asyncio.to_thread(repo.read_all_configs)
        except Exception:
            log.exception("Could not refresh the activity cache after a save")


async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityCog(bot))
