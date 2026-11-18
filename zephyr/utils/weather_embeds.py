"""How every weather message looks.

One module so the slash commands, the prefix commands, /class and the alert
subscriptions share a single visual language:

* a **location line** as the embed author (``📍 Iloilo City, PH``),
* a **condition-coloured** accent instead of flat green/blue,
* the headline number as a markdown heading, with one line of context,
* a tidy three-column grid of secondary readings,
* the provider in the footer and the observation time as the timestamp, which
  Discord renders in each viewer's own time zone.

Everything here is pure: it takes provider data and returns ``discord.Embed``
objects, so it is testable without a network or a gateway.  Values that are
missing come out as "—" rather than raising: partial provider responses used
to crash commands on ``data['wind']['speed']``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord

from zephyr.utils.weather_utils import aqi_label, class_suspension_payload, wmo_description

BRAND = "Zephyr Weather"
OWM = "OpenWeatherMap"
OPEN_METEO = "Open-Meteo"
ICON_URL = "https://openweathermap.org/img/wn/{icon}@4x.png"
DASH = "—"

# Condition palette: an accent that says something about the weather at a glance.
COLORS = {
    "clear": 0xF5A524,
    "clear_night": 0x6366F1,
    "partly": 0x38BDF8,
    "clouds": 0x64748B,
    "fog": 0x94A3B8,
    "drizzle": 0x60A5FA,
    "rain": 0x3B82F6,
    "storm": 0x7C3AED,
    "snow": 0xBAE6FD,
    "unknown": 0x5865F2,
}
ERROR = 0xEF4444
SUCCESS = 0x22C55E
WARNING = 0xF97316
NEUTRAL = 0x5865F2

EMOJI = {
    "clear": "☀️", "clear_night": "🌙", "partly": "⛅", "clouds": "☁️", "fog": "🌫️",
    "drizzle": "🌦️", "rain": "🌧️", "storm": "⛈️", "snow": "❄️", "unknown": "🌡️",
}

AQI_STYLE = {
    1: ("🟢", 0x22C55E, "Air quality is great — enjoy the outdoors."),
    2: ("🟡", 0x84CC16, "Acceptable. Unusually sensitive people may notice it."),
    3: ("🟠", 0xEAB308, "Sensitive groups should ease off long outdoor exertion."),
    4: ("🔴", 0xF97316, "Unhealthy. Limit time outside and consider a mask."),
    5: ("🟣", 0xEF4444, "Very unhealthy. Stay indoors where you can."),
}

_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
_BEAUFORT = [(1, "Calm"), (6, "Light air"), (12, "Light breeze"), (20, "Gentle breeze"),
             (29, "Moderate breeze"), (39, "Fresh breeze"), (50, "Strong breeze"), (62, "Near gale"),
             (75, "Gale"), (89, "Strong gale"), (103, "Storm"), (118, "Violent storm")]


# ---------------------------------------------------------------- small helpers
def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def temp(value, *, unit: str = "°C") -> str:
    value = _num(value)
    return DASH if value is None else f"{round(value)}{unit}"


def pct(value) -> str:
    value = _num(value)
    return DASH if value is None else f"{round(value)}%"


def kmh(value) -> str:
    value = _num(value)
    return DASH if value is None else f"{round(value)} km/h"


def compass(degrees) -> str:
    degrees = _num(degrees)
    return "" if degrees is None else _COMPASS[int((degrees % 360) / 22.5 + 0.5) % 16]


def beaufort(speed_kmh) -> str:
    speed_kmh = _num(speed_kmh)
    if speed_kmh is None:
        return DASH
    return next((name for limit, name in _BEAUFORT if speed_kmh < limit), "Hurricane force")


def meter(value, maximum: float = 100, width: int = 10) -> str:
    """A compact gauge such as ``▰▰▰▱▱▱▱▱▱▱``."""
    value = _num(value)
    if value is None or maximum <= 0:
        return "▱" * width
    filled = max(0, min(width, round(value / maximum * width)))
    return "▰" * filled + "▱" * (width - filled)


def dew_point(temp_c, humidity) -> float | None:
    """Magnus approximation, good to a few tenths of a degree."""
    import math

    temp_c, humidity = _num(temp_c), _num(humidity)
    if temp_c is None or not humidity:
        return None
    gamma = (17.62 * temp_c) / (243.12 + temp_c) + math.log(humidity / 100)
    return 243.12 * gamma / (17.62 - gamma)


def comfort(dew) -> str:
    if dew is None:
        return ""
    for limit, label in ((10, "Dry"), (16, "Comfortable"), (18, "Pleasant"), (21, "Sticky"), (24, "Muggy")):
        if dew < limit:
            return label
    return "Oppressive"


def _clip(text: str, limit: int) -> str:
    text = text or DASH
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _brand(embed: discord.Embed, source: str, extra: str | None = None) -> discord.Embed:
    embed.set_footer(text=" • ".join(part for part in (BRAND, source, extra) if part))
    return embed


# ---------------------------------------------------------------- condition themes
def owm_group(data: dict) -> str:
    weather = (data.get("weather") or [{}])[0]
    code = _num(weather.get("id"))
    night = str(weather.get("icon") or "").endswith("n")
    if code is None:
        return text_group(weather.get("main"))
    if 200 <= code < 300:
        return "storm"
    if 300 <= code < 400:
        return "drizzle"
    if 500 <= code < 600:
        return "rain"
    if 600 <= code < 700:
        return "snow"
    if 700 <= code < 800:
        return "fog"
    if code == 800:
        return "clear_night" if night else "clear"
    if code in (801, 802):
        return "partly"
    return "clouds"


def wmo_theme_group(code) -> str:
    code = _num(code)
    if code is None:
        return "unknown"
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly"
    if code == 3:
        return "clouds"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 57:
        return "drizzle"
    if 61 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "unknown"


def text_group(text) -> str:
    """For OpenWeatherMap's one-word conditions ("Rain", "Clouds", ...)."""
    text = (text or "").lower()
    for needle, group in (("thunder", "storm"), ("storm", "storm"), ("drizzle", "drizzle"), ("rain", "rain"),
                          ("snow", "snow"), ("mist", "fog"), ("fog", "fog"), ("haze", "fog"), ("smoke", "fog"),
                          ("cloud", "clouds"), ("overcast", "clouds"), ("clear", "clear"), ("sun", "clear")):
        if needle in text:
            return group
    return "unknown"


def day_group(day: dict) -> str:
    if day.get("weather_code") is not None:
        return wmo_theme_group(day.get("weather_code"))
    return text_group(day.get("description"))


# ---------------------------------------------------------------- OpenWeatherMap helpers
def place(data: dict, fallback: str = "") -> str:
    name = data.get("name") or fallback or "Unknown place"
    country = (data.get("sys") or {}).get("country")
    return f"{name}, {country}" if country else name


def local_clock(unix, offset_seconds) -> str:
    """A time in the *place's* own zone, e.g. ``5:47 AM``."""
    unix = _num(unix)
    if unix is None:
        return DASH
    zone = timezone(timedelta(seconds=int(_num(offset_seconds) or 0)))
    stamp = datetime.fromtimestamp(unix, zone)
    return stamp.strftime("%I:%M %p").lstrip("0")


def observed_at(data: dict) -> datetime | None:
    unix = _num(data.get("dt"))
    return datetime.fromtimestamp(unix, timezone.utc) if unix is not None else None


def _base(data: dict, title: str, *, fallback_city: str = "", color: int | None = None) -> discord.Embed:
    group = owm_group(data)
    embed = discord.Embed(title=title, color=color if color is not None else COLORS[group],
                          timestamp=observed_at(data))
    embed.set_author(name=f"📍 {place(data, fallback_city)}")
    icon = ((data.get("weather") or [{}])[0]).get("icon")
    if icon:
        embed.set_thumbnail(url=ICON_URL.format(icon=icon))
    return embed


def _condition(data: dict) -> tuple[str, str]:
    weather = (data.get("weather") or [{}])[0]
    description = (weather.get("description") or weather.get("main") or "Unknown").capitalize()
    return EMOJI[owm_group(data)], description


def _wind(data: dict) -> tuple[float | None, float | None, str]:
    wind = data.get("wind") or {}
    speed = _num(wind.get("speed"))
    gust = _num(wind.get("gust"))
    # OpenWeatherMap's metric units are m/s; everything Zephyr shows is km/h.
    return (speed * 3.6 if speed is not None else None,
            gust * 3.6 if gust is not None else None,
            compass(wind.get("deg")))


def _precip_line(data: dict) -> str | None:
    parts = []
    for key, label in (("rain", "🌧️ Rain"), ("snow", "❄️ Snow")):
        amount = _num((data.get(key) or {}).get("1h"))
        if amount:
            parts.append(f"{label} **{amount:g} mm** in the last hour")
    return "\n".join(parts) or None


# ---------------------------------------------------------------- current conditions
def current_embed(data: dict, *, aqi: int | None = None, fallback_city: str = "") -> discord.Embed:
    """``/weather`` and ``/search``: the full picture in one card."""
    main = data.get("main") or {}
    emoji, description = _condition(data)
    speed, gust, direction = _wind(data)
    offset = data.get("timezone")
    sys = data.get("sys") or {}

    embed = _base(data, f"{emoji}  {description}", fallback_city=fallback_city)
    lines = [f"## {temp(main.get('temp'))}",
             f"Feels like **{temp(main.get('feels_like'))}**  ·  "
             f"↑ {temp(main.get('temp_max'), unit='°')}  ↓ {temp(main.get('temp_min'), unit='°')}"]
    precip = _precip_line(data)
    if precip:
        lines.append(precip)
    embed.description = "\n".join(lines)

    wind_value = f"{kmh(speed)} {direction}".strip()
    if gust:
        wind_value += f"\nGusts {kmh(gust)}"
    visibility = _num(data.get("visibility"))
    aqi_value = DASH if aqi is None else f"{AQI_STYLE.get(aqi, ('⚪',))[0]} {aqi_label(aqi)}"

    embed.add_field(name="💧 Humidity", value=pct(main.get("humidity")), inline=True)
    embed.add_field(name="💨 Wind", value=wind_value, inline=True)
    embed.add_field(name="🧭 Pressure", value=f"{main['pressure']} hPa" if _num(main.get("pressure")) else DASH,
                    inline=True)
    embed.add_field(name="👁️ Visibility", value=f"{visibility / 1000:g} km" if visibility is not None else DASH,
                    inline=True)
    embed.add_field(name="☁️ Clouds", value=pct((data.get("clouds") or {}).get("all")), inline=True)
    embed.add_field(name="🍃 Air quality", value=aqi_value, inline=True)
    embed.add_field(name="🌅 Sunrise", value=local_clock(sys.get("sunrise"), offset), inline=True)
    embed.add_field(name="🌇 Sunset", value=local_clock(sys.get("sunset"), offset), inline=True)
    embed.add_field(name="🕒 Local time", value=local_clock(data.get("dt"), offset), inline=True)
    return _brand(embed, OWM)


METRICS = ("temperature", "description", "humidity", "pressure", "windspeed", "precipitation")


def metric_embed(data: dict, metric: str, *, fallback_city: str = "") -> discord.Embed:
    """One reading, big, with a line of context — for the single-metric commands."""
    main = data.get("main") or {}
    emoji, description = _condition(data)
    speed, gust, direction = _wind(data)

    if metric == "temperature":
        title = "🌡️ Temperature"
        headline = temp(main.get("temp"))
        context = (f"Feels like **{temp(main.get('feels_like'))}**  ·  "
                   f"↑ {temp(main.get('temp_max'), unit='°')}  ↓ {temp(main.get('temp_min'), unit='°')}")
    elif metric == "description":
        title = "🌤️ Conditions"
        headline = f"{emoji} {description}"
        context = f"{temp(main.get('temp'))}  ·  Cloud cover {pct((data.get('clouds') or {}).get('all'))}"
    elif metric == "humidity":
        title = "💧 Humidity"
        headline = pct(main.get("humidity"))
        dew = dew_point(_num(main.get("temp")), _num(main.get("humidity")))
        context = (f"Dew point **{temp(dew)}**  ·  {comfort(dew)}" if dew is not None else "No dew point available")
        headline = f"{headline}\n`{meter(main.get('humidity'))}`"
    elif metric == "pressure":
        title = "🧭 Pressure"
        value = _num(main.get("pressure"))
        headline = f"{value:g} hPa" if value is not None else DASH
        trend = DASH if value is None else ("Low — unsettled weather likely" if value < 1005
                                           else "High — settled weather likely" if value > 1020 else "Normal")
        context = trend
    elif metric == "windspeed":
        title = "💨 Wind"
        headline = f"{kmh(speed)} {direction}".strip()
        context = f"{beaufort(speed)}" + (f"  ·  Gusts **{kmh(gust)}**" if gust else "")
    elif metric == "precipitation":
        title = "🌧️ Precipitation"
        precip = _precip_line(data)
        headline = "Dry" if not precip else ""
        context = precip or "No rain or snow in the last hour."
        context += f"\nHumidity {pct(main.get('humidity'))}  ·  Cloud cover {pct((data.get('clouds') or {}).get('all'))}"
    else:  # pragma: no cover - programming error
        raise ValueError(f"unknown metric {metric!r}")

    embed = _base(data, title, fallback_city=fallback_city)
    embed.description = (f"## {headline}\n" if headline else "") + context
    return _brand(embed, OWM)


def air_embed(data: dict, entry: dict | None, *, fallback_city: str = "") -> discord.Embed:
    """``/air``: the 1-5 index as a badge, then the pollutants that drive it."""
    if not entry:
        embed = _base(data, "🍃 Air quality", fallback_city=fallback_city, color=NEUTRAL)
        embed.description = "Air-quality data is not available for this place right now."
        return _brand(embed, OWM)

    aqi = _num((entry.get("main") or {}).get("aqi"))
    dot, color, advice = AQI_STYLE.get(aqi, ("⚪", NEUTRAL, "No guidance available."))
    embed = _base(data, "🍃 Air quality", fallback_city=fallback_city, color=color)
    embed.set_thumbnail(url=None)
    embed.description = (f"## {dot} {aqi_label(aqi)}\n"
                         f"`{meter(aqi, 5, 5)}`  index {aqi or DASH} of 5\n{advice}")
    components = entry.get("components") or {}
    for key, label in (("pm2_5", "PM2.5"), ("pm10", "PM10"), ("o3", "O₃"), ("no2", "NO₂"),
                       ("so2", "SO₂"), ("co", "CO"), ("nh3", "NH₃"), ("no", "NO")):
        value = _num(components.get(key))
        # N/A, not 0: a missing reading is not a clean one.
        embed.add_field(name=label, value=f"`{value:.1f}` µg/m³" if value is not None else "N/A", inline=True)
    embed.add_field(name="​", value="​", inline=True)
    return _brand(embed, OWM)


# ---------------------------------------------------------------- forecast
def _day_label(date: str, fmt: str = "%a %d %b") -> str:
    try:
        return datetime.strptime(date, "%Y-%m-%d").strftime(fmt)
    except (TypeError, ValueError):
        return date or DASH


def _day_condition(day: dict) -> str:
    return day.get("description") or wmo_description(day.get("weather_code"))


def forecast_embeds(location: str, days: list[dict], *, fallback: bool = False) -> list[discord.Embed]:
    """An overview page, then one detail page per day."""
    source = f"{OWM} (fallback)" if fallback else OPEN_METEO
    first_group = day_group(days[0]) if days else "unknown"

    overview = discord.Embed(title=f"🗓️ {len(days)}-day forecast", color=COLORS[first_group])
    overview.set_author(name=f"📍 {location}")
    lows = [d.get("temp_min") for d in days if _num(d.get("temp_min")) is not None]
    highs = [d.get("temp_max") for d in days if _num(d.get("temp_max")) is not None]
    if lows and highs:
        overview.description = f"Ranging **{temp(min(lows))}** to **{temp(max(highs))}** over the period."
    for day in days:
        group = day_group(day)
        overview.add_field(
            name=f"{EMOJI[group]} {_day_label(day.get('date'))}",
            value=(f"**{temp(day.get('temp_max'), unit='°')}** / {temp(day.get('temp_min'), unit='°')}\n"
                   f"{_clip(_day_condition(day), 40)}\n"
                   f"💧 {pct(day.get('precipitation_probability'))}  💨 {kmh(day.get('wind_speed_max'))}"),
            inline=True,
        )
    pages = [_brand(overview, source)]

    for day in days:
        group = day_group(day)
        embed = discord.Embed(title=f"{EMOJI[group]}  {_day_label(day.get('date'), '%A, %B %d')}",
                              color=COLORS[group])
        embed.set_author(name=f"📍 {location}")
        embed.description = (f"## {temp(day.get('temp_max'), unit='°')} / {temp(day.get('temp_min'), unit='°')}\n"
                             f"{_clip(_day_condition(day), 200)}")
        rain = day.get("precipitation_probability")
        embed.add_field(name="🥵 Feels like",
                        value=f"{temp(day.get('feels_like_max'), unit='°')} / {temp(day.get('feels_like_min'), unit='°')}",
                        inline=True)
        embed.add_field(name="🌧️ Rain chance", value=f"{pct(rain)}\n`{meter(rain)}`", inline=True)
        embed.add_field(name="💨 Max wind",
                        value=f"{kmh(day.get('wind_speed_max'))}\n{beaufort(day.get('wind_speed_max'))}", inline=True)
        pages.append(_brand(embed, source))
    return pages


# ---------------------------------------------------------------- class suspension
SUSPENSION_STYLE = {
    "certain": ("🔴", "Classes will almost certainly be suspended", 0xDC2626),
    "likely": ("🟠", "High chance of suspension", 0xF97316),
    "possible": ("🟡", "Suspension is possible", 0xEAB308),
    "none": ("🟢", "Classes will likely resume", 0x22C55E),
    "unknown": ("⚪", "Not enough data to say", NEUTRAL),
}


def class_embed(title: str, data: dict, *, location: str = "Iloilo City", fallback: bool = False) -> discord.Embed:
    """One day of the ``/class`` outlook."""
    feels = _num(data.get("feels_like"))
    payload = class_suspension_payload(feels)
    dot, verdict, color = SUSPENSION_STYLE[payload["level"]]
    condition = data.get("condition") or wmo_description(data.get("weather_code"))
    group = text_group(condition) if data.get("weather_code") is None else wmo_theme_group(data.get("weather_code"))

    embed = discord.Embed(title=f"🏫 Class outlook — {title}", color=color)
    embed.set_author(name=f"📍 {location}")
    # The heat index runs roughly 25-55°C here; the meter spans that range.
    heat = None if feels is None else max(0.0, feels - 25)
    embed.description = (f"## {dot} {verdict}\n"
                         f"{payload['reason'] or 'Comfortable conditions'}  ·  {EMOJI[group]} {condition}\n"
                         f"Heat index `{meter(heat, 30)}` **{temp(feels)}**")
    embed.add_field(name="🌡️ Temperature", value=temp(data.get("temp")), inline=True)
    embed.add_field(name="🥵 Heat index", value=temp(feels), inline=True)
    if data.get("humidity") is not None:
        embed.add_field(name="💧 Humidity", value=pct(data.get("humidity")), inline=True)
    if data.get("precipitation_probability") is not None:
        embed.add_field(name="🌧️ Rain chance", value=pct(data.get("precipitation_probability")), inline=True)
    if data.get("wind_speed") is not None:
        embed.add_field(name="💨 Wind", value=kmh(data.get("wind_speed")), inline=True)
    embed.set_footer(text=f"Advisory only — confirm with your school or LGU • "
                          f"{OWM + ' (fallback)' if fallback else OPEN_METEO}")
    return embed


# ---------------------------------------------------------------- typhoon
def typhoon_embed(alert: dict, *, tcws: str, location: str = "Iloilo City") -> discord.Embed:
    embed = discord.Embed(title=f"🌀 {_clip(alert.get('event') or 'Tropical cyclone alert', 240)}",
                          color=COLORS["storm"])
    embed.set_author(name=f"📍 {location}")
    embed.description = f"## ⚠️ {tcws}\n{_clip(alert.get('description') or 'No details were published.', 3500)}"
    start, end = _num(alert.get("start")), _num(alert.get("end"))
    # Discord renders <t:...> in each reader's own time zone.
    embed.add_field(name="🕒 From", value=f"<t:{int(start)}:f>" if start else DASH, inline=True)
    embed.add_field(name="🕘 Until", value=f"<t:{int(end)}:f>" if end else DASH, inline=True)
    if alert.get("sender_name"):
        embed.add_field(name="📣 Issued by", value=_clip(alert["sender_name"], 1024), inline=True)
    return _brand(embed, OWM)


def typhoon_clear_embed(location: str = "Iloilo City") -> discord.Embed:
    embed = discord.Embed(title="✅ No typhoon alerts", color=SUCCESS,
                          description="There are no tropical cyclone alerts in effect right now. Stay safe!")
    embed.set_author(name=f"📍 {location}")
    return _brand(embed, OWM)


# ---------------------------------------------------------------- errors
def error_embed(title: str, message: str) -> discord.Embed:
    embed = discord.Embed(title=f"⚠️ {title}", description=message, color=ERROR)
    return _brand(embed, "")
