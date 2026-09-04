"""Weather that arrives without being asked.

Two loops, because the two problems are different shapes:

* **Digests** are scheduled, so they are *claimed* -- selected and marked run in
  one transaction, so a restart mid-tick cannot post twice.
* **Watches** are conditional, so they are *not* claimed.  A tick where nothing
  crosses a threshold must leave the row untouched; recording it as a run would
  make the next genuine warning look like a duplicate.

All the deciding lives in ``zephyr/utils/weather_alerts.py`` as pure functions,
so the dashboard's preview renders the identical alert rather than an
approximation of it.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from zephyr.db.weather_subs import (
    KINDS,
    SubscriptionError,
    claim_due,
    create,
    delete_sub,
    get,
    snooze,
    list_for_guild,
    list_watched,
    mark_fired,
    normalise_zone,
    parse_local_time,
)
from zephyr.utils import embeds
from zephyr.utils.weather_alerts import DEFAULT_THRESHOLDS, evaluate
from zephyr.utils.weather_utils import (
    WeatherProviderError,
    geocode_search,
    get_openmeteo_bundle,
)
from zephyr.core.logging import get_logger


log = get_logger(__name__)
KIND_LABELS = {
    "daily": "Daily digest",
    "severe": "Severe weather watch",
    "class_suspension": "Class suspension watch",
}
# Severity, expressed in the factory's roles rather than in three of
# discord.Color's constants. A daily digest is information, a severe-weather
# watch is a warning, and a class suspension is the one people act on.
KIND_ACCENTS = {
    "daily": "info",
    "severe": "warning",
    "class_suspension": "error",
}


def alert_embed(alert: dict) -> discord.Embed:
    embed = embeds.build(
        title=alert["title"],
        description=alert.get("summary"),
        accent=KIND_ACCENTS.get(alert["kind"], "info"),
    )
    for field in alert.get("fields") or []:
        embed.add_field(name=field["name"], value=field["value"], inline=True)
    return embed


_DURATION_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
# Two years. A snooze longer than that is a delete somebody has not admitted to,
# and an unbounded value would let one typo mute a subscription until the heat
# death of the guild.
MAX_SNOOZE_SECONDS = 63_072_000


def _parse_duration(value: str) -> int | None:
    """"2h" -> 7200. Returns None when it cannot be read, 0 to unmute.

    A shorthand rather than a date, because the question is "how long" and
    nobody wants to compute a timestamp to skip a weekend. Bare digits are read
    as hours, which is the unit people mean when they omit one.
    """
    text = str(value).strip().lower().replace(" ", "")
    if not text:
        return None
    if text in {"0", "off", "none", "clear"}:
        return 0
    if text.isdigit():
        return min(int(text) * 3600, MAX_SNOOZE_SECONDS)
    unit = text[-1]
    if unit not in _DURATION_UNITS or not text[:-1].isdigit():
        return None
    return min(int(text[:-1]) * _DURATION_UNITS[unit], MAX_SNOOZE_SECONDS)


def format_timestamp(when: datetime) -> str:
    """A Discord relative timestamp, so everyone reads it in their own zone.

    The alternative is formatting in the guild's configured timezone, which is
    wrong for everybody who is not in it.
    """
    return f"<t:{int(when.timestamp())}:f> (<t:{int(when.timestamp())}:R>)"


class WeatherAlertsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self._digest_loop.start()
        self._watch_loop.start()

    def cog_unload(self):
        self._digest_loop.cancel()
        self._watch_loop.cancel()

    # ------------------------------------------------------------------
    # Runners
    # ------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def _digest_loop(self):
        """Post the digests whose local time has arrived.

        Every minute, because a subscription is set to a wall-clock minute and a
        coarser loop would post visibly late.  The work is one query that
        normally returns nothing.
        """
        try:
            due = await asyncio.to_thread(claim_due, datetime.now(timezone.utc))
        except Exception as exc:
            log.exception("Could not claim due subscriptions")
            return
        for subscription in due:
            await self._deliver(subscription)

    @tasks.loop(minutes=15)
    async def _watch_loop(self):
        """Check the conditional subscriptions.

        Fifteen minutes is the plan's interval: often enough to be a warning,
        rare enough that the provider is not hammered on behalf of every server.
        """
        try:
            watched = await asyncio.to_thread(list_watched)
        except Exception as exc:
            log.exception("Could not read watched subscriptions")
            return
        for subscription in watched:
            await self._deliver(subscription, dedupe=True)

    @_digest_loop.before_loop
    @_watch_loop.before_loop
    async def _before_loops(self):
        await self.bot.wait_until_ready()

    async def _deliver(self, subscription: dict, *, dedupe: bool = False, mark: bool = True) -> bool:
        """Evaluate one subscription and post it if there is anything to say.

        Returns whether anything was actually posted, so /weather-run can say
        so. ``mark=False`` skips the fingerprint write for a manual send.

        Failures are contained per subscription: one unreachable channel or one
        provider hiccup must not stop the rest of the batch, which is why this
        swallows rather than raises.
        """
        try:
            bundle = await asyncio.to_thread(
                get_openmeteo_bundle,
                subscription["lat"],
                subscription["lon"],
                units=subscription.get("units") or "metric",
            )
        except WeatherProviderError as exc:
            log.warning("Weather provider failed for subscription %s: %s", subscription["id"], exc)
            return False
        except Exception:
            log.exception("Could not evaluate subscription %s", subscription["id"])
            return False

        alert = evaluate(
            subscription["kind"],
            bundle,
            location=subscription["location"],
            units=subscription.get("units") or "metric",
            thresholds=subscription.get("thresholds"),
        )
        if alert is None:
            return False
        if dedupe and alert["fingerprint"] == subscription.get("last_fingerprint"):
            return False

        channel = self.bot.get_channel(int(subscription["channel_id"]))
        if channel is None:
            log.warning("Channel %s is not reachable", subscription["channel_id"])
            return False
        try:
            await channel.send(embed=alert_embed(alert))
        except discord.Forbidden:
            # Posting is not permitted any more. Left enabled deliberately: a
            # permission change is usually temporary, and silently disabling the
            # subscription would be discovered much later than a missing message.
            log.warning("Cannot post in channel %s", subscription["channel_id"])
            return False
        except Exception:
            log.exception("Could not deliver subscription %s", subscription["id"])
            return False

        if dedupe and mark:
            # Only recorded once something was actually posted, so a quiet tick
            # never masks the next real warning.
            await asyncio.to_thread(mark_fired, subscription["id"], fingerprint=alert["fingerprint"])
        return True

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @app_commands.command(name="weather-subscribe", description="Have Zephyr post weather to a channel on a schedule.")
    @app_commands.describe(
        kind="What to post", location="City or place name", channel="Where to post it",
        at="Local time for a daily digest, e.g. 08:00", tz="IANA timezone, e.g. Asia/Manila",
    )
    @app_commands.choices(kind=[app_commands.Choice(name=KIND_LABELS[key], value=key) for key in KINDS])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def subscribe(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        location: str,
        channel: discord.TextChannel = None,
        at: str = "08:00",
        tz: str = "UTC",
    ):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        destination = channel or interaction.channel
        me = interaction.guild.me
        if me and not destination.permissions_for(me).send_messages:
            await interaction.response.send_message(
                f"❌ I cannot post in {destination.mention}. Pick a channel I can write to.", ephemeral=True)
            return

        await interaction.response.defer()
        schedule = None
        if kind.value == "daily":
            try:
                schedule = parse_local_time(at).strftime("%H:%M")
            except SubscriptionError as exc:
                await interaction.followup.send(f"❌ {exc}")
                return

        try:
            places = await asyncio.to_thread(geocode_search, location, 1)
        except WeatherProviderError:
            await interaction.followup.send("❌ The geocoder is unavailable; try again shortly.")
            return
        if not places:
            await interaction.followup.send(f"❌ I could not find **{location}**.")
            return
        place = places[0]
        zone_name, zone_accepted = normalise_zone(tz)

        values = {
            "guild_id": str(interaction.guild.id),
            "channel_id": str(destination.id),
            "kind": kind.value,
            # The geocoder's own name, so what is stored is what was resolved --
            # not what was typed, which may have matched something else entirely.
            "location": place.get("name") or location,
            "lat": place["latitude"],
            "lon": place["longitude"],
            "units": "metric",
            "schedule_local_time": schedule,
            "tz": zone_name,
            "thresholds": dict(DEFAULT_THRESHOLDS) if kind.value == "severe" else None,
            "enabled": True,
        }
        try:
            created = await asyncio.to_thread(create, values)
        except SubscriptionError as exc:
            await interaction.followup.send(f"❌ {exc}")
            return
        except Exception as exc:
            log.exception("Could not create a weather subscription")
            await interaction.followup.send(f"❌ Could not save that subscription: {exc}")
            return

        when = f" at {schedule} ({zone_name})" if schedule else ""
        note = "" if zone_accepted else f"\n⚠️ `{tz}` is not an IANA timezone name, so UTC was used instead."
        await interaction.followup.send(embed=embeds.success(
            f"✅ **{KIND_LABELS[kind.value]}** for **{created['location']}** will post in "
            f"{destination.mention}{when}. (#{created['id']}){note}"))

    @app_commands.command(name="weather-subs", description="List this server's weather subscriptions.")
    async def list_subs(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        rows = await asyncio.to_thread(list_for_guild, str(interaction.guild.id))
        if not rows:
            await interaction.followup.send("No weather subscriptions here yet. Add one with `/weather-subscribe`.", ephemeral=True)
            return
        embed = embeds.info(title="🌦️ Weather subscriptions")
        for row in rows[:25]:
            when = f" at {row['schedule_local_time']} ({row['tz']})" if row["schedule_local_time"] else ""
            state = "" if row["enabled"] else " • disabled"
            embed.add_field(
                name=f"#{row['id']} — {KIND_LABELS.get(row['kind'], row['kind'])}",
                value=f"{row['location']} → <#{row['channel_id']}>{when}{state}",
                inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="weather-unsubscribe", description="Remove a weather subscription by its number.")
    @app_commands.describe(subscription_id="The number shown by /weather-subs")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def unsubscribe(self, interaction: discord.Interaction, subscription_id: int):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        existing = await asyncio.to_thread(get, subscription_id)
        # Guild-scoped on purpose: ids are sequential across the whole database,
        # so without this check a guess would delete another server's row.
        if existing is None or existing["guild_id"] != str(interaction.guild.id):
            await interaction.followup.send(f"❌ There is no subscription #{subscription_id} here.", ephemeral=True)
            return
        await asyncio.to_thread(delete_sub, subscription_id)
        await interaction.followup.send(f"🗑️ Removed subscription #{subscription_id}.", ephemeral=True)

    @app_commands.command(name="weather-snooze", description="Mute a subscription for a while without deleting it.")
    @app_commands.describe(
        subscription_id="The number shown by /weather-subs",
        duration="How long — e.g. 2h, 3d, 1w. Use 0 to unmute.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def snooze_command(self, interaction: discord.Interaction, subscription_id: int, duration: str):
        """Snoozing is not disabling.

        `enabled=False` was the only way to stop a subscription, and it is a
        decision to stop -- somebody going away for a week wants the settings
        and the schedule kept and only wants quiet meanwhile. A snoozed row
        also resumes on its *normal* schedule rather than firing once
        immediately, because it is skipped without being claimed.
        """
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        seconds = _parse_duration(duration)
        if seconds is None:
            await interaction.followup.send(
                "❌ I could not read that duration. Try `2h`, `3d` or `1w` — or `0` to unmute.",
                ephemeral=True,
            )
            return

        existing = await asyncio.to_thread(get, subscription_id)
        # Guild-scoped, like /weather-unsubscribe: ids are sequential across the
        # whole database, so without this a guess would mute another server's row.
        if existing is None or existing["guild_id"] != str(interaction.guild.id):
            await interaction.followup.send(f"❌ There is no subscription #{subscription_id} here.", ephemeral=True)
            return

        until = None if seconds == 0 else datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await asyncio.to_thread(snooze, subscription_id, until)

        if until is None:
            await interaction.followup.send(f"🔔 Subscription #{subscription_id} is active again.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"🔇 Subscription #{subscription_id} is muted until {format_timestamp(until)}.",
                ephemeral=True,
            )

    @app_commands.command(name="weather-run", description="Send a subscription's next post right now.")
    @app_commands.describe(subscription_id="The number shown by /weather-subs")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def run_now(self, interaction: discord.Interaction, subscription_id: int):
        """Actually post, unlike /weather-preview.

        /weather-preview shows the caller privately what a subscription *would*
        say, which answers "is this configured correctly" but not "can the bot
        post in that channel". This delivers through the same `_deliver` path
        the scheduler uses, so a permissions problem or an unreachable channel
        surfaces now rather than at 8am.

        Deliberately does not mark the row fired: this is a manual send, and
        advancing `last_run_at` would silently cancel today's real digest.
        """
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        existing = await asyncio.to_thread(get, subscription_id)
        if existing is None or existing["guild_id"] != str(interaction.guild.id):
            await interaction.followup.send(f"❌ There is no subscription #{subscription_id} here.", ephemeral=True)
            return

        delivered = await self._deliver(existing, mark=False)
        if delivered:
            await interaction.followup.send(
                f"📤 Sent subscription #{subscription_id} to <#{existing['channel_id']}>.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"Subscription #{subscription_id} had nothing to report right now, "
                "or I could not post in its channel — check the log if this is unexpected.",
                ephemeral=True,
            )

    @app_commands.command(name="weather-preview", description="Show what a subscription would post right now.")
    @app_commands.describe(subscription_id="The number shown by /weather-subs")
    async def preview(self, interaction: discord.Interaction, subscription_id: int):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        subscription = await asyncio.to_thread(get, subscription_id)
        if subscription is None or subscription["guild_id"] != str(interaction.guild.id):
            await interaction.followup.send(f"❌ There is no subscription #{subscription_id} here.", ephemeral=True)
            return
        try:
            bundle = await asyncio.to_thread(
                get_openmeteo_bundle, subscription["lat"], subscription["lon"],
                units=subscription.get("units") or "metric")
        except WeatherProviderError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        alert = evaluate(
            subscription["kind"], bundle, location=subscription["location"],
            units=subscription.get("units") or "metric", thresholds=subscription.get("thresholds"))
        if alert is None:
            await interaction.followup.send(
                "Nothing to report right now — this subscription would stay quiet.", ephemeral=True)
            return
        await interaction.followup.send(embed=alert_embed(alert), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(WeatherAlertsCog(bot))
