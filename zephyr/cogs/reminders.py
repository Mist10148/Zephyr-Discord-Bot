"""/remindme, /reminders and /reminder-cancel.

The delivery machinery is the weather scheduler's, reused rather than
reinvented: a one-minute ``tasks.loop`` that claims a batch inside one
transaction, and a ``_deliver`` that contains every per-row failure so one bad
reminder cannot stop the rest of the batch. That containment is the reason the
weather loop has never wedged, and it is worth having twice.
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands, tasks

from zephyr.core.logging import get_logger
from zephyr.db import reminders as repo
from zephyr.db.reminders import MAX_PENDING_PER_USER, MIN_REPEAT_SECONDS, ReminderError
from zephyr.db.weather_subs import read_bot_user
from zephyr.utils import embeds

log = get_logger(__name__)

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}
_DURATION = re.compile(r"(\d+)\s*([a-z]+)")
# Two years. A reminder further out than that is a calendar entry, and an
# unbounded value lets one typo park a row in the table forever.
MAX_DELAY_SECONDS = 63_072_000
MIN_DELAY_SECONDS = 30
# Discord's embed field ceiling is 25; a listing longer than that needs paging,
# which a reminder list does not warrant at MAX_PENDING_PER_USER=50.
LIST_LIMIT = 25


def parse_delay(value: str) -> int | None:
    """"90m", "1h30m", "2 days" -> seconds. None when unreadable.

    A duration rather than a timestamp, because "remind me in an hour" is the
    thing people actually want and a timestamp needs a timezone to mean
    anything. Compound forms are accepted since "1h30m" is how anybody would
    write ninety minutes.
    """
    text = str(value).strip().lower()
    if not text:
        return None
    matches = _DURATION.findall(text)
    if not matches:
        return None
    total = 0
    for amount, unit in matches:
        if unit not in _UNITS:
            return None
        total += int(amount) * _UNITS[unit]
    # Consumed entirely, so "1h and also nonsense" is refused rather than
    # silently read as one hour.
    if _DURATION.sub("", text).strip(" ,and"):
        return None
    return total or None


def format_reminder(row: dict) -> str:
    due = row["due_at"]
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    stamp = int(due.timestamp())
    repeat = row.get("repeat_every_seconds")
    # A Discord relative timestamp, so everybody reads it in their own zone --
    # formatting in the *creator's* zone would be wrong for everyone else in
    # the channel.
    suffix = f" · repeats every {_humanise(repeat)}" if repeat else ""
    return f"<t:{stamp}:f> (<t:{stamp}:R>){suffix}"


def _humanise(seconds: int) -> str:
    for unit, size in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds % size == 0:
            count = seconds // size
            return f"{count} {unit}{'s' if count != 1 else ''}"
    return f"{seconds}s"


class RemindersCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self._reminder_loop.start()

    def cog_unload(self):
        self._reminder_loop.cancel()

    @tasks.loop(minutes=1)
    async def _reminder_loop(self):
        try:
            due = await asyncio.to_thread(repo.claim_due, datetime.now(timezone.utc))
        except Exception:
            log.exception("Could not claim due reminders")
            return
        for row in due:
            await self._deliver(row)

    @_reminder_loop.before_loop
    async def _before_reminder_loop(self):
        await self.bot.wait_until_ready()

    @_reminder_loop.error
    async def _reminder_loop_error(self, error):
        """A raising tasks.loop is cancelled, not retried.

        Without this the loop would stop silently on any error the body did not
        catch, and reminders would simply never fire again until a restart.
        """
        log.exception("The reminder loop stopped unexpectedly", exc_info=error)
        self._reminder_loop.restart()

    async def _deliver(self, row: dict) -> bool:
        """Send one reminder. Every failure is contained to this row."""
        try:
            destination = self.bot.get_channel(int(row["channel_id"]))
            if destination is None and row["guild_id"] is None:
                # A DM reminder: the channel is not in the cache, so resolve the
                # user instead.
                user = self.bot.get_user(int(row["user_id"]))
                destination = user or await self.bot.fetch_user(int(row["user_id"]))
            if destination is None:
                log.warning("Reminder %s has no reachable destination", row["id"])
                return False

            await destination.send(
                content=f"<@{row['user_id']}>",
                embed=embeds.info(row["message"], title="⏰ Reminder"),
            )
        except discord.Forbidden:
            # Left claimed rather than retried: a permission problem will not
            # resolve itself between ticks, and retrying would mean sending it
            # the moment it does, hours late.
            log.warning("Cannot deliver reminder %s to %s", row["id"], row["channel_id"])
            return False
        except Exception:
            log.exception("Could not deliver reminder %s", row["id"])
            return False

        if row.get("repeat_every_seconds"):
            try:
                await asyncio.to_thread(
                    repo.reschedule, row["id"], from_time=datetime.now(timezone.utc)
                )
            except Exception:
                log.exception("Could not reschedule repeating reminder %s", row["id"])
        return True

    # ---- commands -------------------------------------------------------

    @app_commands.command(name="remindme", description="Remind you about something later.")
    @app_commands.describe(
        when="How long from now — e.g. 20m, 2h, 1h30m, 3 days",
        message="What to remind you about",
        repeat="Optional: repeat this often — e.g. 1d, 1w",
    )
    async def remindme(
        self,
        interaction: discord.Interaction,
        when: str,
        message: str,
        repeat: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        delay = parse_delay(when)
        if delay is None:
            await interaction.followup.send(
                "❌ I could not read that. Try `20m`, `2h`, `1h30m` or `3 days`.", ephemeral=True
            )
            return
        if delay < MIN_DELAY_SECONDS:
            await interaction.followup.send(
                f"❌ That is too soon — use at least {MIN_DELAY_SECONDS} seconds.", ephemeral=True
            )
            return
        if delay > MAX_DELAY_SECONDS:
            await interaction.followup.send(
                "❌ That is too far out. Two years is the limit.", ephemeral=True
            )
            return

        interval = None
        if repeat:
            interval = parse_delay(repeat)
            if interval is None:
                await interaction.followup.send(
                    "❌ I could not read the repeat interval. Try `1d` or `1w`.", ephemeral=True
                )
                return
            if interval < MIN_REPEAT_SECONDS:
                await interaction.followup.send(
                    f"❌ A repeat has to be at least {_humanise(MIN_REPEAT_SECONDS)} apart.",
                    ephemeral=True,
                )
                return

        due_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        try:
            created = await asyncio.to_thread(
                repo.create,
                {
                    "user_id": str(interaction.user.id),
                    "guild_id": str(interaction.guild.id) if interaction.guild else None,
                    "channel_id": str(interaction.channel_id),
                    "message": message.strip()[:1800],
                    "due_at": due_at,
                    "tz": await self._zone_for(interaction.user.id),
                    "repeat_every_seconds": interval,
                    "attempts": 0,
                    "source": "discord",
                },
            )
        except ReminderError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        await interaction.followup.send(
            f"⏰ Reminder **#{created['id']}** set for {format_reminder(created)}.",
            ephemeral=True,
        )

    @app_commands.command(name="reminders", description="List your pending reminders.")
    async def list_reminders(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rows = await asyncio.to_thread(repo.list_pending, str(interaction.user.id))
        if not rows:
            await interaction.followup.send("You have no reminders pending.", ephemeral=True)
            return

        embed = embeds.info(
            f"{len(rows)} of {MAX_PENDING_PER_USER} used.", title="⏰ Your reminders"
        )
        for row in rows[:LIST_LIMIT]:
            embed.add_field(
                name=f"#{row['id']}",
                # Truncated: an embed field value caps at 1024, and a reminder
                # body may be 1800 characters.
                value=f"{row['message'][:180]}\n{format_reminder(row)}",
                inline=False,
            )
        if len(rows) > LIST_LIMIT:
            embed.set_footer(
                text=embeds.footer_text(f"Showing the first {LIST_LIMIT}."),
                icon_url=embeds.icon_url(),
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="reminder-cancel", description="Cancel one of your reminders.")
    @app_commands.describe(reminder_id="The number shown by /reminders")
    async def cancel_reminder(self, interaction: discord.Interaction, reminder_id: int):
        await interaction.response.defer(ephemeral=True)
        # Scoped to the caller inside the statement: ids are sequential across
        # the whole database, so a guess would otherwise cancel somebody
        # else's.
        removed = await asyncio.to_thread(repo.cancel, reminder_id, str(interaction.user.id))
        if removed:
            await interaction.followup.send(f"🗑️ Cancelled reminder #{reminder_id}.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"❌ You have no reminder #{reminder_id}.", ephemeral=True
            )

    @cancel_reminder.autocomplete("reminder_id")
    async def _cancel_autocomplete(self, interaction: discord.Interaction, current: str):
        rows = await asyncio.to_thread(repo.list_pending, str(interaction.user.id))
        term = str(current).strip()
        choices = [
            app_commands.Choice(name=f"#{row['id']} · {row['message'][:80]}", value=row["id"])
            for row in rows
            if not term or term in str(row["id"])
        ]
        return choices[:25]

    async def _zone_for(self, user_id: int) -> str:
        """The caller's own timezone if they set one, else UTC.

        Stored rather than used for scheduling: `due_at` is absolute, and this
        is what lets a listing be rendered in the zone the person thinks in.
        """
        try:
            row = await asyncio.to_thread(read_bot_user, str(user_id))
        except Exception:
            log.warning("Could not read a timezone for %s", user_id, exc_info=True)
            return "UTC"
        name = (row or {}).get("timezone")
        if not name:
            return "UTC"
        try:
            ZoneInfo(str(name))
        except (ZoneInfoNotFoundError, ValueError):
            return "UTC"
        return str(name)


async def setup(bot: commands.Bot):
    await bot.add_cog(RemindersCog(bot))
