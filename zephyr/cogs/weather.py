"""Weather features: prefix commands, slash commands, and the class-suspension
forecast.

Fetching lives here; how every message *looks* lives in
``zephyr.utils.weather_embeds``, which both the slash commands and the legacy
prefix commands use, so the two can no longer drift apart.
"""

import asyncio
from collections import Counter
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlencode

import requests
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View

from zephyr.config import (
    AIR_POLLUTION_URL,
    API_KEY,
    AUTH_ENABLED,
    CURRENT_URL,
    FORECAST_URL,
    ALERTS_URL,
    ILOILO_COORDS,
    WEB_APP_URL,
    WEB_PUBLIC_URL,
)
from zephyr.db.weather_subs import clear_bot_user, read_bot_user, write_bot_user
from zephyr.utils.weather_utils import (
    WeatherProviderError,
    class_suspension_payload,
    tcws_from_alert,
    geocode_search,
    get_openmeteo_daily_forecast,
    get_openmeteo_current,
)
from zephyr.utils import weather_embeds as look
from zephyr.utils.help_data import WEATHER_KEYS, text_summary
from zephyr.utils.pagination import _send_paginated_embeds, send_paginated_ctx


HTTP_TIMEOUT_SECONDS = 10


async def _get_json(url: str) -> dict:
    """GET a JSON document off the event loop, with a timeout.

    requests.get() used to run directly inside the command handlers with no
    timeout, so one slow OpenWeatherMap response froze the whole bot -- music,
    chat and the gateway heartbeat included.  Network failures come back as an
    OWM-shaped error rather than an exception, so callers have one path.
    """
    def fetch():
        response = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        try:
            return response.json()
        except ValueError:
            return {"cod": str(response.status_code), "message": "unreadable response"}

    try:
        data = await asyncio.to_thread(fetch)
    except requests.RequestException as exc:
        # Not str(exc): requests puts the URL, API key included, in the message.
        print(f"[Weather] Request failed: {exc.__class__.__name__}")
        return {"cod": "error", "message": "the weather service could not be reached"}
    return data if isinstance(data, dict) else {"cod": "error", "message": "unexpected response"}


def _owm_url(base: str, **params) -> str:
    """An OpenWeatherMap URL with every parameter encoded.

    The city used to be interpolated raw (``q={city}``), so a name with ``&``,
    ``#`` or ``?`` broke the query or injected parameters of its own.
    """
    query = {"appid": API_KEY, **{key: value for key, value in params.items() if value is not None}}
    return f"{base}?{urlencode(query)}"


class SavedPlace(str):
    """A city name that also carries the coordinates /setlocation resolved.

    A ``str`` so every embed and message that prints the city keeps working,
    while the fetchers can ask OpenWeatherMap by coordinates.  By name alone,
    "San Jose" confirmed as California could come back as Costa Rica, or not
    be found at all when the two geocoders spell a place differently.
    """

    lat: float
    lon: float

    def __new__(cls, name: str, lat: float, lon: float):
        place = super().__new__(cls, name)
        place.lat, place.lon = float(lat), float(lon)
        return place


def _coords(city: str) -> tuple[float, float] | None:
    lat, lon = getattr(city, "lat", None), getattr(city, "lon", None)
    return (lat, lon) if lat is not None and lon is not None else None


def _current_url(city: str) -> str:
    coords = _coords(city)
    if coords:
        return _owm_url(CURRENT_URL, lat=coords[0], lon=coords[1], units="metric")
    return _owm_url(CURRENT_URL, q=city, units="metric")


def _air_url(lat, lon) -> str:
    return _owm_url(AIR_POLLUTION_URL, lat=lat, lon=lon)


def _ok(data: dict) -> bool:
    """OpenWeatherMap success, whichever of 200 / "200" this endpoint uses.

    The old check was ``cod != "404"``, so a 401 (bad key) or 429 (rate limit)
    was treated as weather data and the handler crashed on data['main'].
    """
    return str(data.get("cod")) == "200"


def _failure(data: dict, city: str) -> str:
    if str(data.get("cod")) == "404":
        return f"City {city} not found."
    return f"Couldn't get the weather for {city} right now ({data.get('message') or 'unknown error'}). Try again later."



FALLBACK_CITY = "Iloilo"


# ---------------------------------------------------------------------------
# Shared fetch-and-render helpers (used by slash *and* prefix commands)
# ---------------------------------------------------------------------------
async def _fetch_current(city: str) -> dict:
    return await _get_json(_current_url(city))


async def _aqi_entry(data: dict) -> dict | None:
    coord = data.get("coord") or {}
    if coord.get("lat") is None or coord.get("lon") is None:
        return None
    aqi_data = await _get_json(_air_url(coord["lat"], coord["lon"]))
    entries = aqi_data.get("list") or []
    return entries[0] if entries else None


def _unavailable(data: dict, city: str) -> discord.Embed:
    return look.error_embed("Weather unavailable", _failure(data, city))


async def _current_card(city: str) -> discord.Embed:
    """/weather and /search."""
    data = await _fetch_current(city)
    if not _ok(data):
        return _unavailable(data, city)
    entry = await _aqi_entry(data)
    aqi = (entry or {}).get("main", {}).get("aqi")
    return look.current_embed(data, aqi=aqi, fallback_city=city)


async def _metric_card(city: str, metric: str) -> discord.Embed:
    """/temperature, /description, /humidity, /pressure, /windspeed, /precipitation."""
    data = await _fetch_current(city)
    if not _ok(data):
        return _unavailable(data, city)
    return look.metric_embed(data, metric, fallback_city=city)


async def _air_card(city: str) -> discord.Embed:
    data = await _fetch_current(city)
    if not _ok(data):
        return _unavailable(data, city)
    return look.air_embed(data, await _aqi_entry(data), fallback_city=city)


async def _typhoon_card() -> discord.Embed:
    data = await _get_json(_owm_url(ALERTS_URL, lat=ILOILO_COORDS["lat"], lon=ILOILO_COORDS["lon"],
                                    exclude="minutely,hourly,daily"))
    if "cod" in data and str(data["cod"]) != "200":
        # One Call answers errors with a cod; a success has none.  It also needs
        # its own subscription, so a plain key gets a 401 -- saying "no alerts"
        # then, possibly during a typhoon, would be dangerous.
        return look.error_embed("Typhoon alerts unavailable", _failure(data, "Iloilo City"))
    for alert in data.get("alerts") or []:
        text = f"{alert.get('event', '')} {alert.get('description', '')}".lower()
        if "typhoon" in text or "cyclone" in text:
            return look.typhoon_embed(alert, tcws=tcws_from_alert(alert))
    return look.typhoon_clear_embed()


def _resolve_place(city: str) -> tuple[float, float, str] | None:
    places = geocode_search(city, 1)
    if not places:
        return None
    place = places[0]
    label = ", ".join(part for part in (place.get("name") or city, place.get("country_code") or place.get("country"))
                      if part)
    return place["latitude"], place["longitude"], label


async def _forecast_pages(city: str) -> list[discord.Embed]:
    """/forecast: Open-Meteo first, OpenWeatherMap if that fails."""
    days, label, fallback = [], city, False
    try:
        coords = _coords(city)
        resolved = (coords[0], coords[1], str(city)) if coords else await asyncio.to_thread(_resolve_place, city)
        if resolved:
            lat, lon, label = resolved
            days = await asyncio.to_thread(get_openmeteo_daily_forecast, lat, lon, days=3)
    except Exception as e:
        print(f"[Forecast Open-Meteo Error] {e.__class__.__name__}: {e}")

    if not days:
        try:
            coords = _coords(city)
            if coords:
                # A /setlocation place: looking its name up again could land
                # in another country (San Jose, CA vs San Jose, Costa Rica).
                days = await asyncio.to_thread(_owm_daily_forecast, lat=coords[0], lon=coords[1], days=3)
            else:
                days = await asyncio.to_thread(_owm_daily_forecast, city=str(city), days=3)
            fallback = True
        except Exception as e:
            print(f"[Forecast OWM Error] {e.__class__.__name__}")
            message = str(e) if isinstance(e, WeatherFetchError) else "The forecast could not be loaded. Try again later."
            return [look.error_embed("Forecast unavailable", message)]
    if not days:
        return [look.error_embed("Forecast unavailable", "No forecast data was returned.")]
    return look.forecast_embeds(label, days, fallback=fallback)


# ---------------------------------------------------------------------------
# Weather Prefix Commands
# ---------------------------------------------------------------------------
def _metric_prefix(metric: str):
    async def command(ctx, *, city: str = FALLBACK_CITY):
        async with ctx.typing():
            embed = await _metric_card(city, metric)
        await ctx.send(embed=embed)

    command.__name__ = metric
    return commands.command(name=metric)(command)


temperature = _metric_prefix("temperature")
description = _metric_prefix("description")
humidity = _metric_prefix("humidity")
pressure = _metric_prefix("pressure")
windspeed = _metric_prefix("windspeed")
precipitation = _metric_prefix("precipitation")


def _web_app_link(guild_id: int | None) -> str | None:
    """Where /use should send someone, or None when this deployment has no web app.

    With the dashboard configured, the link is the *sign-in* endpoint rather than the
    site root: it runs the Discord OAuth round trip and drops the user on the page
    they actually wanted, already authenticated, instead of on a signed-out page that
    makes them find the login themselves. `next` is re-validated server-side by
    safe_next(), so the guild id is the only thing interpolated here.

    The base is WEB_PUBLIC_URL and that is *not* interchangeable with WEB_APP_URL:
    OAuth returns to the redirect URI registered with Discord, which config derives
    from WEB_PUBLIC_URL, so signing in via any other host dies at the callback.
    (tests/test_web_config.py pins that separation.)
    """
    if AUTH_ENABLED:
        # In a DM there is no server to open, so land on the server picker.
        next_path = f"/g/{guild_id}" if guild_id else "/g"
        return f"{WEB_PUBLIC_URL.rstrip('/')}/api/v1/auth/login?next={quote(next_path, safe='')}"
    # No dashboard on this deployment: hand out the plain public site if the owner
    # set one. WEB_PUBLIC_URL is deliberately not used as a fallback -- it always
    # carries a localhost default, so it would advertise a dead link to everyone on
    # a deployment that configured nothing.
    return WEB_APP_URL or None


def _web_app_embed(guild_id: int | None = None) -> tuple[discord.Embed, View | None]:
    """The /use response, or an honest message when the deployment has not set one."""
    link = _web_app_link(guild_id)
    if not link:
        return (
            discord.Embed(
                title="Web app not configured",
                description=(
                    "This bot has no web app URL set. Ask the owner to set `WEB_APP_URL`, "
                    "or to configure the dashboard (`DISCORD_CLIENT_ID`, "
                    "`DISCORD_CLIENT_SECRET`, `REDIS_URL`)."
                ),
                color=discord.Color.orange(),
            ),
            None,
        )

    if AUTH_ENABLED:
        embed = discord.Embed(title="Zephyr dashboard", color=discord.Color.green())
        embed.description = (
            "Sign in with Discord to manage this server's music, weather alerts, AI and settings.\n"
            "You will only see servers where you have **Manage Server**."
        )
        label = "Sign in and open dashboard"
    else:
        embed = discord.Embed(title="Weather App Link", color=discord.Color.green())
        embed.description = "Live conditions, the week ahead, and heat-index advisories for any city."
        label = "Open the web app"

    # A link button, not a markdown link: it needs no callback and no custom_id, so
    # timeout=None is free and the button keeps working long after the message ages.
    view = View(timeout=None)
    view.add_item(Button(label=label, url=link, style=discord.ButtonStyle.link))
    return embed, view


@commands.command()
async def use(ctx):
    embed, view = _web_app_embed(ctx.guild.id if ctx.guild else None)
    await ctx.send(embed=embed, view=view)


@commands.command(name="helpweather")
async def helpweather(ctx):
    # Rendered from the same registry as /help, so it can no longer go stale.
    embed = discord.Embed(title="🌦️ Weather Commands", description=text_summary(WEATHER_KEYS),
                          color=look.COLORS["partly"])
    embed.set_footer(text="<required> · [optional] • Slash commands also work: type / to see them")
    await ctx.send(embed=embed)


@commands.command()
async def typhoon(ctx):
    async with ctx.typing():
        embed = await _typhoon_card()
    await ctx.send(embed=embed)


@commands.command()
async def air(ctx, *, city: str = FALLBACK_CITY):
    async with ctx.typing():
        embed = await _air_card(city)
    await ctx.send(embed=embed)


@commands.command()
async def weather(ctx, *, city: str = FALLBACK_CITY):
    async with ctx.typing():
        embed = await _current_card(city)
    await ctx.send(embed=embed)


@commands.command()
async def forecast(ctx, *, city: str = FALLBACK_CITY):
    # Same daily forecast as /forecast.  The old prefix version paged through
    # 6-hourly slots in UTC and paired each with the wrong hour's air quality.
    async with ctx.typing():
        pages = await _forecast_pages(city)
    await send_paginated_ctx(ctx, pages)


@commands.command()
async def search(ctx, *, city: str):
    async with ctx.typing():
        embed = await _current_card(city)
    await ctx.send(embed=embed)


PREFIX_COMMANDS = [
    temperature, description, humidity, pressure, windspeed, use, helpweather,
    precipitation, typhoon, air, weather, forecast, search,
]


# ---------------------------------------------------------------------------
# Class Suspension Helpers
# ---------------------------------------------------------------------------
_SUSPENSION_DECISIONS = {
    "certain": "🔴 **Classes will almost certainly be suspended**",
    "likely": "🟠 **High chance of suspension**",
    "possible": "🟡 **Suspension is possible**",
    "none": "✅ **Classes will likely resume**",
    "unknown": "🤔 **Not enough data to say**",
}


def class_suspension(feels_like: float | None):
    """Return a (decision, reason) tuple from the shared heat-index scale."""
    payload = class_suspension_payload(feels_like)
    return _SUSPENSION_DECISIONS[payload["level"]], payload["reason"] or "Comfortable conditions"


def _class_color(feels_like: float | None) -> discord.Color:
    level = class_suspension_payload(feels_like)["level"]
    return {
        "certain": discord.Color.red(),
        "likely": discord.Color.orange(),
        "possible": discord.Color.gold(),
    }.get(level, discord.Color.green())


def _get_class_weather_data():
    """Fetch current and daily forecast data for Iloilo from Open-Meteo."""
    lat, lon = ILOILO_COORDS["lat"], ILOILO_COORDS["lon"]
    current = get_openmeteo_current(lat, lon)
    daily = get_openmeteo_daily_forecast(lat, lon, days=3)
    return current, daily


def _build_class_embed(title: str, data: dict, *, fallback: bool = False) -> discord.Embed:
    """One day of the /class outlook."""
    return look.class_embed(title, data, fallback=fallback)


# ---------------------------------------------------------------------------
# OpenWeatherMap fallback helpers
# ---------------------------------------------------------------------------
class WeatherFetchError(RuntimeError):
    """A weather lookup failed.  The message is safe to show to users."""


def _owm_fetch(url: str, params: dict, *, timeout: int) -> dict:
    """GET an OpenWeatherMap document without leaking the key.

    ``raise_for_status()`` puts the full request URL -- ``appid`` included --
    in its message, and /forecast and /class showed ``str(e)`` to users.
    """
    try:
        resp = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        print(f"[Weather] OpenWeatherMap request failed: {exc.__class__.__name__}")
        raise WeatherFetchError("The weather service could not be reached.") from None
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    status = getattr(resp, "status_code", 200)
    status = status if isinstance(status, int) else 200
    if status >= 400 or str(data.get("cod", "200")) != "200":
        message = data.get("message") or f"HTTP {status}"
        raise WeatherFetchError(f"OpenWeatherMap: {message}")
    return data


def _owm_daily_forecast(city: str = None, lat: float = None, lon: float = None, days: int = 3) -> list[dict]:
    """Aggregate OpenWeatherMap 3-hour forecast into daily summaries.

    Returns data in the same shape as Open-Meteo's daily forecast.
    """
    params = {"appid": API_KEY, "units": "metric"}
    if city:
        params["q"] = city
    else:
        params["lat"] = lat
        params["lon"] = lon

    data = _owm_fetch(FORECAST_URL, params, timeout=15)
    # Group by the *place's* calendar day.  Grouping in UTC put Iloilo's
    # (UTC+8) day boundary at 08:00 and made "today" a day behind overnight.
    local = timezone(timedelta(seconds=int((data.get("city") or {}).get("timezone") or 0)))
    grouped: dict[str, dict] = {}
    for item in data.get("list", []):
        date = datetime.fromtimestamp(item["dt"], local).strftime("%Y-%m-%d")
        main = item.get("main", {})
        wind = item.get("wind", {})
        pop = item.get("pop") or 0
        weather_list = item.get("weather", [])
        description = weather_list[0].get("main", "Unknown") if weather_list else "Unknown"

        entry = grouped.setdefault(date, {
            "temps": [],
            "feels": [],
            "winds": [],
            "pops": [],
            "conditions": Counter(),
        })
        entry["conditions"][description] += 1
        entry["temps"].append(main.get("temp"))
        entry["feels"].append(main.get("feels_like"))
        entry["winds"].append((wind.get("speed") or 0) * 3.6)
        entry["pops"].append(pop)

    today = datetime.now(local).date()
    result = []
    for i in range(days):
        date = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        g = grouped.get(date)
        temps = [t for t in (g or {}).get("temps", []) if t is not None]
        if not temps:
            # max() of an empty list raised ValueError on a day of null slots.
            continue
        feels = [t for t in g["feels"] if t is not None] or temps
        result.append({
            "date": date,
            "weather_code": None,
            # The most common condition, not whichever 3-hour slot came first
            # (often the small hours, which said little about the day).
            "description": g["conditions"].most_common(1)[0][0],
            "temp_max": max(temps),
            "temp_min": min(temps),
            "feels_like_max": max(feels),
            "feels_like_min": min(feels),
            "precipitation_probability": int(round(max(g["pops"]) * 100)),
            "wind_speed_max": round(max(g["winds"]), 1),
        })
    return result


def _owm_current(lat: float, lon: float) -> dict:
    """Fetch current conditions from OpenWeatherMap for the given coordinates."""
    data = _owm_fetch(CURRENT_URL, {"lat": lat, "lon": lon, "appid": API_KEY, "units": "metric"}, timeout=10)
    weather = (data.get("weather") or [{}])[0]
    main = data.get("main") or {}
    if main.get("temp") is None:
        raise WeatherFetchError("OpenWeatherMap returned no current conditions.")
    return {
        "temp": main.get("temp"),
        "humidity": main.get("humidity"),
        "apparent_temp": main.get("feels_like", main.get("temp")),
        "weather_code": None,
        "condition": weather.get("main", "Unknown"),
        "wind_speed": round((data.get("wind", {}).get("speed") or 0) * 3.6, 1),
    }


# ---------------------------------------------------------------------------
# Weather Slash Cog
# ---------------------------------------------------------------------------
class WeatherCog(commands.Cog):
    #: Used when a caller gives no city and has set no default of their own.
    #: Iloilo City is the bot's home, and was the hardcoded default before
    #: /setlocation existed -- keeping it means nobody's behaviour changed.
    FALLBACK_CITY = FALLBACK_CITY

    def __init__(self, bot):
        self.bot = bot

    async def _city_for(self, interaction: discord.Interaction, city: str | None) -> str:
        """Resolve the city for a command: what was asked for, then the caller's
        own default, then Iloilo.

        Read per invocation rather than cached: these are user-initiated
        commands at human rates, and a cache would keep serving the old city
        for a while after /setlocation, which is precisely the moment somebody
        checks whether it worked.
        """
        if city:
            return city
        try:
            stored = await asyncio.to_thread(read_bot_user, str(interaction.user.id))
        except Exception as exc:
            print(f"[Weather] Could not read the default city: {exc}")
            return self.FALLBACK_CITY
        stored = stored or {}
        name = stored.get("default_city")
        if not name:
            return self.FALLBACK_CITY
        if stored.get("lat") is not None and stored.get("lon") is not None:
            # What /setlocation confirmed is what the weather is for.
            return SavedPlace(name, stored["lat"], stored["lon"])
        return name

    @app_commands.command(name="setlocation", description="Set your default city for weather commands.")
    @app_commands.describe(city="Your city, or leave empty to clear it")
    async def set_location(self, interaction: discord.Interaction, city: str = None):
        await interaction.response.defer(ephemeral=True)
        if not city:
            await asyncio.to_thread(clear_bot_user, str(interaction.user.id))
            await interaction.followup.send(
                f"🧹 Cleared your default city. Weather commands will use {self.FALLBACK_CITY} again.",
                ephemeral=True)
            return

        try:
            places = await asyncio.to_thread(geocode_search, city, 1)
        except WeatherProviderError:
            await interaction.followup.send("❌ The geocoder is unavailable; try again shortly.", ephemeral=True)
            return
        if not places:
            await interaction.followup.send(f"❌ I could not find **{city}**.", ephemeral=True)
            return

        place = places[0]
        # The geocoder's own name and coordinates are stored, not the typed
        # string: what was resolved is what the weather will be for.
        await asyncio.to_thread(write_bot_user, str(interaction.user.id), {
            "default_city": place.get("name") or city,
            "lat": place["latitude"],
            "lon": place["longitude"],
            "timezone": place.get("timezone"),
        })
        where = ", ".join(part for part in (place.get("admin1"), place.get("country")) if part)
        await interaction.followup.send(
            f"📍 Your default city is now **{place.get('name') or city}**{f' ({where})' if where else ''}.",
            ephemeral=True)

    @app_commands.command(name="mylocation", description="Show the default city weather commands use for you.")
    async def my_location(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        stored = await asyncio.to_thread(read_bot_user, str(interaction.user.id))
        if not stored or not stored.get("default_city"):
            await interaction.followup.send(
                f"You have not set one, so commands default to {self.FALLBACK_CITY}. "
                "Set yours with `/setlocation`.", ephemeral=True)
            return
        await interaction.followup.send(
            f"📍 Your default city is **{stored['default_city']}**. Clear it with `/setlocation` and no city.",
            ephemeral=True)

    async def _reply(self, interaction: discord.Interaction, build) -> None:
        """Defer, build the embed, reply: lookups can outlast Discord's 3 seconds."""
        await interaction.response.defer()
        await interaction.followup.send(embed=await build)

    @app_commands.command(name="temperature", description="Get the current temperature of a city.")
    @app_commands.describe(city="City name (defaults to your /setlocation city)")
    async def slash_temperature(self, interaction: discord.Interaction, city: str = None):
        city = await self._city_for(interaction, city)
        await self._reply(interaction, _metric_card(city, "temperature"))

    @app_commands.command(name="description", description="Get the weather description of a city.")
    @app_commands.describe(city="City name (defaults to your /setlocation city)")
    async def slash_description(self, interaction: discord.Interaction, city: str = None):
        city = await self._city_for(interaction, city)
        await self._reply(interaction, _metric_card(city, "description"))

    @app_commands.command(name="humidity", description="Get the humidity of a city.")
    @app_commands.describe(city="City name (defaults to your /setlocation city)")
    async def slash_humidity(self, interaction: discord.Interaction, city: str = None):
        city = await self._city_for(interaction, city)
        await self._reply(interaction, _metric_card(city, "humidity"))

    @app_commands.command(name="pressure", description="Get the atmospheric pressure of a city.")
    @app_commands.describe(city="City name (defaults to your /setlocation city)")
    async def slash_pressure(self, interaction: discord.Interaction, city: str = None):
        city = await self._city_for(interaction, city)
        await self._reply(interaction, _metric_card(city, "pressure"))

    @app_commands.command(name="windspeed", description="Get the wind speed of a city.")
    @app_commands.describe(city="City name (defaults to your /setlocation city)")
    async def slash_windspeed(self, interaction: discord.Interaction, city: str = None):
        city = await self._city_for(interaction, city)
        await self._reply(interaction, _metric_card(city, "windspeed"))

    @app_commands.command(name="use", description="Provides a link to the web application version of this bot.")
    async def slash_use(self, interaction: discord.Interaction):
        embed, view = _web_app_embed(interaction.guild_id)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="precipitation", description="Get precipitation details for a city.")
    @app_commands.describe(city="City name (defaults to your /setlocation city)")
    async def slash_precipitation(self, interaction: discord.Interaction, city: str = None):
        city = await self._city_for(interaction, city)
        await self._reply(interaction, _metric_card(city, "precipitation"))

    @app_commands.command(name="typhoon", description="Get the latest typhoon alert for Iloilo City.")
    async def slash_typhoon(self, interaction: discord.Interaction):
        await self._reply(interaction, _typhoon_card())

    @app_commands.command(name="air", description="Get the air quality for a specific city.")
    @app_commands.describe(city="City name (defaults to your /setlocation city)")
    async def slash_air(self, interaction: discord.Interaction, city: str = None):
        city = await self._city_for(interaction, city)
        await self._reply(interaction, _air_card(city))

    @app_commands.command(name="weather", description="Get current weather, air quality, and precipitation for a city.")
    @app_commands.describe(city="City name (defaults to your /setlocation city)")
    async def slash_weather(self, interaction: discord.Interaction, city: str = None):
        city = await self._city_for(interaction, city)
        await self._reply(interaction, _current_card(city))

    @app_commands.command(name="forecast", description="Get a clean 3-day forecast for a city.")
    @app_commands.describe(city="City name (defaults to your /setlocation city)")
    async def slash_forecast(self, interaction: discord.Interaction, city: str = None):
        city = await self._city_for(interaction, city)
        await interaction.response.defer()
        await _send_paginated_embeds(interaction, await _forecast_pages(city))

    @app_commands.command(name="search", description="Search for current weather and air quality in a city.")
    @app_commands.describe(city="City name (defaults to your /setlocation city)")
    async def slash_search(self, interaction: discord.Interaction, city: str = None):
        city = await self._city_for(interaction, city)
        await self._reply(interaction, _current_card(city))

    @app_commands.command(name="ping", description="Shows the bot's ping")
    async def slash_ping(self, interaction: discord.Interaction):
        latency = round(interaction.client.latency * 1000)
        dot = "🟢" if latency < 150 else "🟡" if latency < 400 else "🔴"
        embed = discord.Embed(title="🏓 Pong!", description=f"## {dot} {latency} ms\nGateway heartbeat latency",
                              color=look.SUCCESS if latency < 150 else look.WARNING)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="class", description="Check the class suspension forecast based on the feels-like temperature.")
    async def class_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        current = None
        daily = []
        fallback = False

        # Try Open-Meteo first
        try:
            current, daily = await asyncio.to_thread(_get_class_weather_data)
        except Exception as e:
            print(f"[Class Open-Meteo Error] {e.__class__.__name__}: {e}")

        # Fall back to OpenWeatherMap for Iloilo
        if not current or not daily or len(daily) < 3:
            try:
                lat, lon = ILOILO_COORDS["lat"], ILOILO_COORDS["lon"]
                current = await asyncio.to_thread(_owm_current, lat, lon)
                daily = await asyncio.to_thread(_owm_daily_forecast, lat=lat, lon=lon, days=3)
                fallback = True
            except Exception as e:
                print(f"[Class OWM Error] {e.__class__.__name__}")
                message = (str(e) if isinstance(e, WeatherFetchError)
                           else "The weather service could not be reached. Try again later.")
                await interaction.followup.send(embed=look.error_embed("Class outlook unavailable", message),
                                                ephemeral=True)
                return

        if not current or not daily:
            await interaction.followup.send(
                embed=look.error_embed("Class outlook unavailable", "Try again later."), ephemeral=True)
            return

        # A full forecast starts with today.  Late at night the OpenWeatherMap
        # fallback has no slots left for today, so it starts with tomorrow --
        # that used to fail the whole command instead of showing what it had.
        has_today = len(daily) >= 3
        later = daily[1:3] if has_today else daily[-2:]
        # Rated on today's *peak* heat index, as the class-suspension watch and
        # the digest are: at 06:00 the current reading (27 °C) said classes
        # resume while the watch, on the forecast 42 °C peak, said suspension.
        today_peaks = [value for value in (current.get("apparent_temp"),
                                           daily[0].get("feels_like_max") if has_today else None)
                       if isinstance(value, (int, float))]
        pages = [("Today", {
            "temp": current["temp"],
            "feels_like": max(today_peaks) if today_peaks else None,
            "humidity": current["humidity"],
            "precipitation_probability": daily[0].get("precipitation_probability") if has_today else None,
            "wind_speed": current["wind_speed"],
            "weather_code": current.get("weather_code"),
            "condition": current.get("condition"),
        })]
        for label, day in zip(("Tomorrow", "In two days"), later):
            pages.append((label, {
                "temp": day["temp_max"],
                "feels_like": day["feels_like_max"],
                "humidity": None,
                "precipitation_probability": day["precipitation_probability"],
                "wind_speed": day["wind_speed_max"],
                "weather_code": day.get("weather_code"),
                "condition": day.get("description"),
            }))
        embeds = [_build_class_embed(title, data, fallback=fallback) for title, data in pages]
        await _send_paginated_embeds(interaction, embeds, ephemeral=True)


async def setup(bot: commands.Bot):
    for command in PREFIX_COMMANDS:
        bot.add_command(command)
    await bot.add_cog(WeatherCog(bot))
