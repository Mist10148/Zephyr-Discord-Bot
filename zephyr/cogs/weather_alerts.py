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
import traceback
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from zephyr.db.weather_subs import (
    KINDS,
    WATCHED_KINDS,
    SubscriptionError,
    claim_due,
    clear_fingerprint,
    create,
    delete_sub,
    get,
    list_for_guild,
    list_watched,
    mark_fired,
    release_claim,
    normalise_zone,
    parse_local_time,
)
from zephyr.utils import weather_embeds as look
from zephyr.utils.weather_alerts import (
    DEFAULT_THRESHOLDS,
    evaluate,
    fingerprint_to_store,
    is_heat_advisory,
    is_repeat,
    should_forget,
)
from zephyr.utils.weather_utils import (
    WeatherProviderError,
    geocode_search,
    get_openmeteo_bundle,
)

KIND_LABELS = {
    "daily": "Daily digest",
    "severe": "Severe weather watch",
    "class_suspension": "Class suspension watch",
}
KIND_COLORS = {
    "daily": 0x38BDF8,
    "severe": 0xF97316,
    "class_suspension": 0xDC2626,
}
KIND_ICONS = {"daily": "📰", "severe": "⚠️", "class_suspension": "🏫"}
FIELD_ICONS = {
    "High / Low": "🌡️", "Feels like": "🥵", "Chance of rain": "🌧️", "Max wind": "💨",
    "Right now": "🕒", "Watch window": "⏱️", "Feels like (max)": "🥵",
}


def alert_embed(alert: dict) -> discord.Embed:
    """A scheduled post or a /weather-preview, in the shared weather style."""
    kind = alert.get("kind")
    embed = discord.Embed(
        title=alert["title"],
        description=alert.get("summary"),
        color=KIND_COLORS.get(kind, look.NEUTRAL),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(name=f"{KIND_ICONS.get(kind, '🔔')} {KIND_LABELS.get(kind, 'Weather alert')}")
    advisory = None
    for field in alert.get("fields") or []:
        if field["name"] == "Advisory only":
            advisory = field["value"]
            continue
        icon = FIELD_ICONS.get(field["name"])
        embed.add_field(name=f"{icon} {field['name']}" if icon else field["name"], value=field["value"], inline=True)
    # The digest always computed today's class-suspension outlook but never showed it.
    suspension = alert.get("class_suspension") or {}
    # Heat only: the line says "heat index advisory", and a cool day is not one.
    if kind == "daily" and is_heat_advisory(suspension):
        dot, verdict, _ = look.SUSPENSION_STYLE[suspension["level"]]
        embed.add_field(name="🏫 Classes", value=f"{dot} {verdict} (heat index advisory)", inline=False)
    footer = "Advisory only — confirm with your school or LGU" if advisory else None
    embed.set_footer(text=" • ".join(part for part in (look.BRAND, look.OPEN_METEO, footer) if part))
    return embed


class WeatherAlertsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Answer every failed command.

        Without this a member lacking Manage Server saw "The application did
        not respond" from /weather-subscribe, and a database error after
        defer() left "Zephyr is thinking..." on screen for good.
        """
        if isinstance(error, app_commands.MissingPermissions):
            message = "❌ You need the **Manage Server** permission to change weather subscriptions."
        elif isinstance(error, app_commands.CheckFailure):
            message = "❌ You can't use that here."
        else:
            original = getattr(error, "original", error)
            traceback.print_exception(type(original), original, original.__traceback__)
            message = "❌ Something went wrong with that weather subscription command. Try again shortly."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception as exc:
            print(f"[WeatherAlerts] Could not report a command error: {exc}")

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
            print(f"[WeatherAlerts] Could not claim due subscriptions: {exc}")
            return
        for subscription in due:
            if not await self._deliver_safely(subscription):
                # Claimed but not delivered for a reason that may pass (provider
                # hiccup, failed send): make it due again instead of losing today's.
                try:
                    await asyncio.to_thread(release_claim, subscription["id"], subscription.get("last_run_at"))
                except Exception as exc:
                    print(f"[WeatherAlerts] Could not release subscription {subscription['id']}: {exc}")

    @tasks.loop(minutes=15)
    async def _watch_loop(self):
        """Check the conditional subscriptions.

        Fifteen minutes is the plan's interval: often enough to be a warning,
        rare enough that the provider is not hammered on behalf of every server.
        """
        try:
            watched = await asyncio.to_thread(list_watched)
        except Exception as exc:
            print(f"[WeatherAlerts] Could not read watched subscriptions: {exc}")
            return
        for subscription in watched:
            await self._deliver_safely(subscription, dedupe=True)

    @_digest_loop.before_loop
    @_watch_loop.before_loop
    async def _before_loops(self):
        await self.bot.wait_until_ready()

    async def _deliver_safely(self, subscription: dict, *, dedupe: bool = False) -> bool:
        """_deliver, with a backstop the docstring below always promised.

        An exception escaping a tasks.loop body stops that loop for good, so
        one bad row (a malformed threshold, a transient error in mark_fired)
        used to end severe-weather watches -- or daily digests -- for every
        guild until the bot restarted.
        """
        try:
            return await self._deliver(subscription, dedupe=dedupe)
        except Exception:
            print(f"[WeatherAlerts] Subscription {subscription.get('id')} failed; continuing with the rest.")
            traceback.print_exc()
            return False

    async def _deliver(self, subscription: dict, *, dedupe: bool = False) -> bool:
        """Evaluate one subscription and post it if there is anything to say.

        Failures are contained per subscription: one unreachable channel or one
        provider hiccup must not stop the rest of the batch, which is why this
        swallows rather than raises.

        Returns False only for failures worth retrying (the provider, or a send
        that errored); a missing channel or a lost permission returns True,
        because retrying every minute would not fix it.
        """
        try:
            bundle = await asyncio.to_thread(
                get_openmeteo_bundle,
                subscription["lat"],
                subscription["lon"],
                units=subscription.get("units") or "metric",
            )
        except WeatherProviderError as exc:
            print(f"[WeatherAlerts] Provider failed for subscription {subscription['id']}: {exc}")
            return False
        except Exception:
            traceback.print_exc()
            return False

        alert = evaluate(
            subscription["kind"],
            bundle,
            location=subscription["location"],
            units=subscription.get("units") or "metric",
            thresholds=subscription.get("thresholds"),
        )
        if alert is None:
            if (dedupe and subscription.get("last_fingerprint")
                    and should_forget(subscription["kind"], subscription.get("last_run_at"))):
                # Quiet past the watch's window: the next storm is news, even if
                # it looks exactly like the last one.  Quiet *inside* it is the
                # same weather wavering, and forgetting then reposted it.
                await asyncio.to_thread(clear_fingerprint, subscription["id"])
            return True
        if dedupe and is_repeat(alert, subscription.get("last_fingerprint")):
            return True

        try:
            channel_id = int(str(subscription["channel_id"]))
        except (TypeError, ValueError):
            print(f"[WeatherAlerts] Subscription {subscription['id']} has an invalid channel id.")
            return True
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            print(f"[WeatherAlerts] Channel {subscription['channel_id']} is not reachable.")
            return True
        # The dashboard only checks that channel_id looks like an id; get_channel
        # finds a channel in *any* guild the bot is in.  Never post across guilds.
        channel_guild = getattr(channel, "guild", None)
        if channel_guild is None or str(channel_guild.id) != str(subscription["guild_id"]):
            print(f"[WeatherAlerts] Channel {subscription['channel_id']} is not in guild "
                  f"{subscription['guild_id']}; skipping subscription {subscription['id']}.")
            return True
        try:
            await channel.send(embed=alert_embed(alert))
        except discord.Forbidden:
            # Posting is not permitted any more. Left enabled deliberately: a
            # permission change is usually temporary, and silently disabling the
            # subscription would be discovered much later than a missing message.
            print(f"[WeatherAlerts] Cannot post in {subscription['channel_id']}.")
            return True
        except discord.HTTPException as exc:
            traceback.print_exc()
            # A 4xx (missing Embed Links, a bad payload) fails identically every
            # minute, re-fetching the forecast each time, until local midnight.
            # Only server errors are worth retrying.
            return not (exc.status is None or exc.status >= 500)
        except Exception:
            traceback.print_exc()
            return False

        if dedupe:
            # Only recorded once something was actually posted, so a quiet tick
            # never masks the next real warning.
            await asyncio.to_thread(mark_fired, subscription["id"],
                                    fingerprint=fingerprint_to_store(alert, subscription.get("last_fingerprint")))
        return True

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @app_commands.command(name="weather-subscribe", description="Have Zephyr post weather to a channel on a schedule.")
    @app_commands.describe(
        kind="What to post", location="City or place name", channel="Where to post it",
        at="Local time for a daily digest, e.g. 08:00",
        tz="IANA timezone, e.g. Asia/Manila (defaults to the location's own)",
    )
    @app_commands.choices(kind=[app_commands.Choice(name=KIND_LABELS[key], value=key) for key in KINDS])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def subscribe(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        # Bounded: a 6,000-character value was saved, then echoed back past
        # Discord's limits, so the reply failed and a retry said "already exists".
        location: app_commands.Range[str, 1, 100],
        channel: discord.TextChannel = None,
        at: app_commands.Range[str, 1, 5] = "08:00",
        tz: app_commands.Range[str, 1, 64] = None,
    ):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        destination = channel or interaction.channel
        if isinstance(destination, discord.Thread):
            # A thread leaves the bot's cache once it archives, and from then
            # on every post was dropped with only a console line to show for it.
            await interaction.response.send_message(
                "❌ Subscriptions post to a channel, not a thread. Run this in the channel, "
                "or pick one with `channel:`.", ephemeral=True)
            return
        me = interaction.guild.me
        permissions = destination.permissions_for(me) if me else None
        if permissions is not None and not (permissions.send_messages and permissions.embed_links):
            await interaction.response.send_message(
                f"❌ I cannot post embeds in {destination.mention} (I need Send Messages and Embed Links). "
                "Pick a channel I can write to.", ephemeral=True)
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
        # Default to the place's own zone, as the dashboard does: a UTC default
        # posted an "08:00" Iloilo digest at 16:00 Manila time.
        zone_name, zone_accepted = normalise_zone(tz or place.get("timezone") or "UTC")

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
        except Exception:
            # The raw database error (SQL, connection details) is for the log,
            # not a public channel.
            traceback.print_exc()
            await interaction.followup.send("❌ Could not save that subscription. Try again shortly.")
            return

        when = f" at {schedule} ({zone_name})" if schedule else ""
        note = "" if zone_accepted else f"\n⚠️ `{tz}` is not an IANA timezone name, so UTC was used instead."
        await interaction.followup.send(embed=discord.Embed(
            description=f"✅ **{KIND_LABELS[kind.value]}** for **{created['location']}** will post in "
                        f"{destination.mention}{when}. (#{created['id']}){note}",
            color=discord.Color.green()))

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
        embed = discord.Embed(
            title="🌦️ Weather subscriptions",
            description=f"{len(rows)} subscription{'s' if len(rows) != 1 else ''} in **{interaction.guild.name}**",
            color=0x38BDF8)
        shown = rows[:24]
        for row in shown:
            when = f"\n⏰ {row['schedule_local_time']} ({row['tz']})" if row["schedule_local_time"] else ""
            state = "" if row["enabled"] else "\n⏸️ Disabled"
            embed.add_field(
                name=f"{KIND_ICONS.get(row['kind'], '🔔')} #{row['id']} · {KIND_LABELS.get(row['kind'], row['kind'])}",
                value=f"📍 {row['location']}\n📢 <#{row['channel_id']}>{when}{state}",
                inline=True)
        footer = "Remove one with /weather-unsubscribe • Preview with /weather-preview"
        if len(rows) > len(shown):
            # Discord allows 25 fields; say so instead of silently dropping the rest.
            footer = f"…and {len(rows) - len(shown)} more on the dashboard • " + footer
        embed.set_footer(text=footer)
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
            alert = evaluate(
                subscription["kind"], bundle, location=subscription["location"],
                units=subscription.get("units") or "metric", thresholds=subscription.get("thresholds"))
        except WeatherProviderError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        if alert is None:
            await interaction.followup.send(
                "Nothing to report right now — this subscription would stay quiet.", ephemeral=True)
            return
        if subscription["kind"] in WATCHED_KINDS and is_repeat(alert, subscription.get("last_fingerprint")):
            # The scheduler would stay quiet: this was already announced.
            await interaction.followup.send(
                "Already announced — the watch would not post this again.",
                embed=alert_embed(alert), ephemeral=True)
            return
        await interaction.followup.send(embed=alert_embed(alert), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(WeatherAlertsCog(bot))
