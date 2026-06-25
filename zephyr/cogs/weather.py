"""Weather features: prefix commands, slash commands, and the class-suspension
forecast.

Ported 1:1 from the original bot.py (lines 178-892). Prefix commands are kept as
``ctx``-based functions (added to the bot in ``setup``); slash commands live in
``WeatherCog``; the heat-index ``/class`` command is added as a method on
``WeatherCog``.
"""

from datetime import datetime, timezone, timedelta

import requests
import discord
from discord import app_commands, Embed
from discord.ext import commands
from discord.ui import Button, View

from zephyr.config import (
    API_KEY,
    CURRENT_URL,
    FORECAST_URL,
    ALERTS_URL,
    ILOILO_COORDS,
    WEB_APP_URL,
)
from zephyr.utils.weather_utils import (
    get_tcws_description,
    _format_datetime_pht,
    _format_date_label,
    wmo_description,
    geocode_city,
    get_openmeteo_daily_forecast,
    get_openmeteo_current,
)
from zephyr.utils.pagination import _send_paginated_embeds


# ---------------------------------------------------------------------------
# Weather Prefix Commands
# ---------------------------------------------------------------------------
@commands.command()
async def temperature(ctx, *, city: str = "Iloilo"):
    current_data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
    embed = discord.Embed(title=f"Temperature in {city}", color=discord.Color.blue())
    if current_data.get("cod") != "404":
        embed.add_field(name="Current Temperature", value=f"{current_data['main'].get('temp', 'N/A')}°C", inline=False)
    else:
        embed.description = f"City {city} not found."
        embed.color = discord.Color.red()
    await ctx.send(embed=embed)


@commands.command()
async def description(ctx, *, city: str = "Iloilo"):
    current_data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
    embed = discord.Embed(title=f"Weather Description in {city}", color=discord.Color.blue())
    if current_data.get("cod") != "404":
        desc = current_data['weather'][0].get('description', 'N/A')
        embed.add_field(name="Current Weather", value=desc.capitalize(), inline=False)
    else:
        embed.description = f"City {city} not found."
        embed.color = discord.Color.red()
    await ctx.send(embed=embed)


@commands.command()
async def humidity(ctx, *, city: str = "Iloilo"):
    current_data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
    embed = discord.Embed(title=f"Humidity in {city}", color=discord.Color.blue())
    if current_data.get("cod") != "404":
        embed.add_field(name="Current Humidity", value=f"{current_data['main'].get('humidity', 'N/A')}%", inline=False)
    else:
        embed.description = f"City {city} not found."
        embed.color = discord.Color.red()
    await ctx.send(embed=embed)


@commands.command()
async def pressure(ctx, *, city: str = "Iloilo"):
    current_data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
    embed = discord.Embed(title=f"Pressure in {city}", color=discord.Color.blue())
    if current_data.get("cod") != "404":
        embed.add_field(name="Current Pressure", value=f"{current_data['main'].get('pressure', 'N/A')} hPa", inline=False)
    else:
        embed.description = f"City {city} not found."
        embed.color = discord.Color.red()
    await ctx.send(embed=embed)


@commands.command()
async def windspeed(ctx, *, city: str = "Iloilo"):
    current_data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
    embed = discord.Embed(title=f"Wind Speed in {city}", color=discord.Color.blue())
    if current_data.get("cod") != "404":
        embed.add_field(name="Current Wind Speed", value=f"{current_data['wind'].get('speed', 'N/A')} m/s", inline=False)
    else:
        embed.description = f"City {city} not found."
        embed.color = discord.Color.red()
    await ctx.send(embed=embed)


@commands.command()
async def use(ctx):
    embed = discord.Embed(title="Weather App Link", color=discord.Color.green())
    embed.description = f"[Click here to access the app]({WEB_APP_URL})"
    await ctx.send(embed=embed)


@commands.command(name="helpweather")
async def helpweather(ctx):
    help_message = (
        "**List of Available Commands:**\n"
        "`/typhoon` - Get typhoon alerts\n"
        "`/weather <city>` - Current weather of a city\n"
        "`/forecast <city>` - 3-day weather forecast\n"
        "`/temperature <city>` - Current temperature\n"
        "`/description <city>` - Weather description\n"
        "`/air <city>` - Air quality\n"
        "`/humidity <city>` - Humidity\n"
        "`/pressure <city>` - Pressure\n"
        "`/windspeed <city>` - Wind speed\n"
        "`/precipitation <city>` - Precipitation\n"
        "`/search <query>` - Search weather info\n"
        "`/use` - Link to web app\n"
        "`/helpweather` - Show this help message"
    )
    embed = discord.Embed(title="Weather Bot Help", description=help_message, color=discord.Color.gold())
    await ctx.send(embed=embed)


@commands.command()
async def precipitation(ctx, *, city: str = "Iloilo"):
    current_data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
    embed = discord.Embed(title=f"Precipitation in {city}", color=discord.Color.blue())
    if current_data.get("cod") != "404":
        rain = current_data.get('rain')
        snow = current_data.get('snow')
        pop = current_data.get('pop')
        msg = ""
        if pop is not None:
            msg += f"Probability: {pop * 100:.2f}%\n"
        if rain:
            msg += f"Rain: {rain.get('1h', 'N/A')} mm in the last hour\n"
        if snow:
            msg += f"Snow: {snow.get('1h', 'N/A')} mm in the last hour\n"
        embed.description = msg or "No significant precipitation reported."
    else:
        embed.description = f"City {city} not found."
        embed.color = discord.Color.red()
    await ctx.send(embed=embed)


@commands.command()
async def typhoon(ctx):
    alert_data = requests.get(f"{ALERTS_URL}?lat={ILOILO_COORDS['lat']}&lon={ILOILO_COORDS['lon']}&appid={API_KEY}").json()
    embed = discord.Embed(title="Typhoon Alert for Iloilo City", color=discord.Color.orange())
    alerts = alert_data.get("alerts", [])
    if alerts:
        for alert in alerts:
            if "typhoon" in alert.get("description", "").lower():
                name = alert.get('event', 'Unknown Typhoon')
                wind = alert.get('wind_speed', 0)
                tcws = get_tcws_description(wind)
                embed.description = (
                    f"**Typhoon Name:** {name}\n"
                    f"**Description:** {alert.get('description', 'N/A')}\n"
                    f"**Start Time:** {datetime.utcfromtimestamp(alert.get('start', 0)).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                    f"**End Time:** {datetime.utcfromtimestamp(alert.get('end', 0)).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                    f"**TCWS Status:** {tcws}"
                )
                await ctx.send(embed=embed)
                return
    embed.description = "**No typhoon alerts for Iloilo City at the moment.**"
    await ctx.send(embed=embed)


@commands.command()
async def air(ctx, *, city: str = "Iloilo"):
    current_data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
    embed = discord.Embed(title=f"Air Quality in {city}", color=discord.Color.green())
    if current_data.get("cod") != "404":
        lat, lon = current_data['coord']['lat'], current_data['coord']['lon']
        aqi_data = requests.get(f"http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}").json()
        if 'list' in aqi_data and aqi_data['list']:
            aqi = aqi_data['list'][0]['main']['aqi']
            desc = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}.get(aqi, "Unknown")
            comp = aqi_data['list'][0]['components']
            pollutants = (
                f"CO: {comp.get('co', 'N/A')} µg/m³\n"
                f"NO: {comp.get('no', 'N/A')} µg/m³\n"
                f"NO₂: {comp.get('no2', 'N/A')} µg/m³\n"
                f"O₃: {comp.get('o3', 'N/A')} µg/m³\n"
                f"SO₂: {comp.get('so2', 'N/A')} µg/m³\n"
                f"NH₃: {comp.get('nh3', 'N/A')} µg/m³\n"
                f"PM2.5: {comp.get('pm2_5', 'N/A')} µg/m³\n"
                f"PM10: {comp.get('pm10', 'N/A')} µg/m³"
            )
            embed.description = f"**Air Quality:** {desc}\n\n**Pollutants:**\n{pollutants}"
        else:
            embed.description = "Air quality data not available."
            embed.color = discord.Color.yellow()
    else:
        embed.description = f"City {city} not found."
        embed.color = discord.Color.red()
    await ctx.send(embed=embed)


@commands.command()
async def weather(ctx, *, city: str = "Iloilo"):
    current_data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
    embed = discord.Embed(title=f"Weather in {city}", color=discord.Color.blurple())
    if current_data.get("cod") != "404":
        desc = current_data['weather'][0]['description']
        temp = current_data['main']['temp']
        hum = current_data['main']['humidity']
        wind = current_data['wind']['speed']
        rain = current_data.get('rain')
        snow = current_data.get('snow')
        pop = current_data.get('pop')
        embed.add_field(name="Description", value=desc.capitalize(), inline=False)
        embed.add_field(name="Temperature", value=f"{temp}°C", inline=True)
        embed.add_field(name="Humidity", value=f"{hum}%", inline=True)
        embed.add_field(name="Wind Speed", value=f"{wind} m/s", inline=True)
        precip = ""
        if pop is not None:
            precip += f"Probability: {pop * 100:.2f}%\n"
        if rain:
            precip += f"Rain: {rain.get('1h', 'N/A')} mm/hr\n"
        if snow:
            precip += f"Snow: {snow.get('1h', 'N/A')} mm/hr\n"
        embed.add_field(name="Precipitation", value=precip or "No significant precipitation reported.", inline=False)

        lat, lon = current_data['coord']['lat'], current_data['coord']['lon']
        aqi_data = requests.get(f"http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}").json()
        if 'list' in aqi_data and aqi_data['list']:
            aqi = aqi_data['list'][0]['main']['aqi']
            aqi_desc = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}.get(aqi, "Unknown")
            embed.add_field(name="Air Quality", value=aqi_desc, inline=False)
        else:
            embed.add_field(name="Air Quality", value="Data not available", inline=False)
    else:
        embed.description = f"City {city} not found."
        embed.color = discord.Color.red()
    await ctx.send(embed=embed)


class ForecastButton(discord.ui.Button):
    def __init__(self, label, style, forecast_data, index, view_ref):
        super().__init__(label=label, style=style)
        self.forecast_data = forecast_data
        self.index = index
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction):
        self.view_ref.current_index = self.index
        await self.view_ref.update_embed(interaction)


class ForecastView(discord.ui.View):
    def __init__(self, forecast_data, air_quality_list, city):
        super().__init__(timeout=120)
        self.forecast_data = forecast_data
        self.air_quality_list = air_quality_list
        self.city = city
        self.current_index = 0

        self.prev_button = discord.ui.Button(label="◀ Previous", style=discord.ButtonStyle.grey, disabled=True)
        self.next_button = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.grey, disabled=len(forecast_data) <= 1)

        async def prev_callback(interaction: discord.Interaction):
            self.current_index -= 1
            await self.update_embed(interaction)

        async def next_callback(interaction: discord.Interaction):
            self.current_index += 1
            await self.update_embed(interaction)

        self.prev_button.callback = prev_callback
        self.next_button.callback = next_callback
        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    def update_buttons(self):
        self.prev_button.disabled = self.current_index == 0
        self.next_button.disabled = self.current_index == len(self.forecast_data) - 1

    async def update_embed(self, interaction: discord.Interaction):
        forecast = self.forecast_data[self.current_index]
        date = datetime.utcfromtimestamp(forecast['dt']).strftime('%Y-%m-%d %H:%M:%S UTC')
        temp = forecast['main'].get('temp', 'N/A')
        desc = forecast['weather'][0].get('description', 'N/A')
        hum = forecast['main'].get('humidity', 'N/A')
        pres = forecast['main'].get('pressure', 'N/A')
        wind = forecast['wind'].get('speed', 'N/A')

        aqi_data = self.air_quality_list[self.current_index] if self.current_index < len(self.air_quality_list) else None
        aqi = aqi_data['main']['aqi'] if aqi_data else None
        aqi_desc = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}.get(aqi, "Unknown")

        embed = discord.Embed(title=f"Forecast for {self.city} - {date}", color=discord.Color.green())
        embed.add_field(name="Temperature", value=f"{temp}°C", inline=True)
        embed.add_field(name="Description", value=desc.capitalize(), inline=True)
        embed.add_field(name="Humidity", value=f"{hum}%", inline=True)
        embed.add_field(name="Pressure", value=f"{pres} hPa", inline=True)
        embed.add_field(name="Wind Speed", value=f"{wind} m/s", inline=True)
        embed.add_field(name="Air Quality", value=aqi_desc, inline=False)

        self.update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)


@commands.command()
async def forecast(ctx, *, city: str = "Iloilo"):
    forecast_data = requests.get(f"{FORECAST_URL}?appid={API_KEY}&q={city}&units=metric").json()
    if forecast_data.get("cod") == "200":
        lat, lon = forecast_data['city']['coord']['lat'], forecast_data['city']['coord']['lon']
        aqi_data = requests.get(f"http://api.openweathermap.org/data/2.5/air_pollution/forecast?lat={lat}&lon={lon}&appid={API_KEY}").json()
        aqi_list = aqi_data.get('list', [])
        forecasts = forecast_data['list'][:24][::2]

        if forecasts:
            view = ForecastView(forecasts, aqi_list[:len(forecasts)], city)
            first = forecasts[0]
            date = datetime.utcfromtimestamp(first['dt']).strftime('%Y-%m-%d %H:%M:%S UTC')
            embed = discord.Embed(title=f"Forecast for {city} - {date}", color=discord.Color.green())
            embed.add_field(name="Temperature", value=f"{first['main'].get('temp', 'N/A')}°C", inline=True)
            embed.add_field(name="Description", value=first['weather'][0].get('description', 'N/A').capitalize(), inline=True)
            embed.add_field(name="Humidity", value=f"{first['main'].get('humidity', 'N/A')}%", inline=True)
            embed.add_field(name="Pressure", value=f"{first['main'].get('pressure', 'N/A')} hPa", inline=True)
            embed.add_field(name="Wind Speed", value=f"{first['wind'].get('speed', 'N/A')} m/s", inline=True)
            aqi = aqi_list[0]['main']['aqi'] if aqi_list else None
            embed.add_field(name="Air Quality", value={1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}.get(aqi, "Unknown"), inline=False)
            await ctx.send(embed=embed, view=view)
        else:
            await ctx.send("No forecast data available.")
    else:
        await ctx.send(f"City {city} not found.")


@commands.command()
async def search(ctx, *, city: str):
    current_data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
    embed = discord.Embed(title=f"Search Results for {city}", color=discord.Color.dark_gold())
    if current_data.get("cod") == 200:
        temp = current_data['main'].get('temp', 'N/A')
        desc = current_data['weather'][0].get('description', 'N/A')
        hum = current_data['main'].get('humidity', 'N/A')
        pres = current_data['main'].get('pressure', 'N/A')
        wind = current_data['wind'].get('speed', 'N/A')
        date = datetime.fromtimestamp(current_data['dt'], timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        embed.add_field(name="Date", value=date, inline=False)
        embed.add_field(name="Temperature", value=f"{temp}°C", inline=True)
        embed.add_field(name="Description", value=desc.capitalize(), inline=True)
        embed.add_field(name="Humidity", value=f"{hum}%", inline=True)
        embed.add_field(name="Pressure", value=f"{pres} hPa", inline=True)
        embed.add_field(name="Wind Speed", value=f"{wind} m/s", inline=True)

        lat, lon = current_data['coord']['lat'], current_data['coord']['lon']
        aqi_data = requests.get(f"http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}").json()
        if 'list' in aqi_data and aqi_data['list']:
            aqi = aqi_data['list'][0]['main']['aqi']
            embed.add_field(name="Air Quality", value={1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}.get(aqi, "Unknown"), inline=False)
    else:
        embed.description = f"City {city} not found."
        embed.color = discord.Color.red()
    await ctx.send(embed=embed)


PREFIX_COMMANDS = [
    temperature, description, humidity, pressure, windspeed, use, helpweather,
    precipitation, typhoon, air, weather, forecast, search,
]


# ---------------------------------------------------------------------------
# Class Suspension Helpers
# ---------------------------------------------------------------------------
def class_suspension(feels_like: float):
    """Return a (decision, description) tuple based on the feels-like temperature."""
    if feels_like >= 50:
        return "🔴 **Class will certainly be suspended!**", "Excessive Heat"
    elif feels_like >= 41:
        return "🟠 **High possibility of suspension**", "Very Hot"
    elif feels_like >= 38:
        return "🟡 **Low possibility of suspension**", "Hot Conditions"
    elif feels_like >= 15:
        return "✅ **Classes will likely resume**", "Pleasant Weather"
    else:
        return "🤔 **Unusual temperature reading**", "Unusual Weather"


def _class_color(feels_like: float) -> discord.Color:
    if feels_like >= 50:
        return discord.Color.red()
    elif feels_like >= 41:
        return discord.Color.orange()
    elif feels_like >= 38:
        return discord.Color.gold()
    return discord.Color.green()


def _get_class_weather_data():
    """Fetch current and daily forecast data for Iloilo from Open-Meteo."""
    lat, lon = ILOILO_COORDS["lat"], ILOILO_COORDS["lon"]
    current = get_openmeteo_current(lat, lon)
    daily = get_openmeteo_daily_forecast(lat, lon, days=3)
    return current, daily


def _build_class_embed(title: str, data: dict) -> Embed:
    """Build a clean class-suspension embed for one day."""
    feels_like = data["feels_like"]
    suspension, desc = class_suspension(feels_like)
    condition = data.get("condition") or wmo_description(data.get("weather_code"))
    embed = Embed(
        title=f"📅 Class Suspension Forecast: {title}",
        description=f"**{condition}**",
        color=_class_color(feels_like),
    )
    embed.add_field(name="🌡 Temperature", value=f"{data['temp']}°C", inline=True)
    embed.add_field(name="🥵 Feels Like", value=f"{feels_like}°C", inline=True)
    if data.get("humidity") is not None:
        embed.add_field(name="💧 Humidity", value=f"{data['humidity']}%", inline=True)
    if data.get("precipitation_probability") is not None:
        embed.add_field(name="🌧 Rain Chance", value=f"{data['precipitation_probability']}%", inline=True)
    if data.get("wind_speed") is not None:
        embed.add_field(name="💨 Wind", value=f"{data['wind_speed']} km/h", inline=True)
    embed.add_field(name="📢 Decision", value=f"{suspension}\n{desc}", inline=False)
    return embed


# ---------------------------------------------------------------------------
# OpenWeatherMap fallback helpers
# ---------------------------------------------------------------------------
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

    resp = requests.get(FORECAST_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if str(data.get("cod")) != "200":
        raise RuntimeError(data.get("message", "OpenWeatherMap forecast unavailable"))

    grouped: dict[str, dict] = {}
    for item in data.get("list", []):
        date = datetime.utcfromtimestamp(item["dt"]).strftime("%Y-%m-%d")
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
            "description": description,
        })
        entry["temps"].append(main.get("temp"))
        entry["feels"].append(main.get("feels_like"))
        entry["winds"].append((wind.get("speed") or 0) * 3.6)
        entry["pops"].append(pop)

    today = datetime.utcnow().date()
    result = []
    for i in range(days):
        date = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        g = grouped.get(date)
        if not g or not g["temps"]:
            continue
        result.append({
            "date": date,
            "weather_code": None,
            "description": g["description"],
            "temp_max": max(t for t in g["temps"] if t is not None),
            "temp_min": min(t for t in g["temps"] if t is not None),
            "feels_like_max": max(t for t in g["feels"] if t is not None),
            "feels_like_min": min(t for t in g["feels"] if t is not None),
            "precipitation_probability": int(max(g["pops"]) * 100),
            "wind_speed_max": max(g["winds"]),
        })
    return result


def _owm_current(lat: float, lon: float) -> dict:
    """Fetch current conditions from OpenWeatherMap for the given coordinates."""
    resp = requests.get(
        CURRENT_URL,
        params={"lat": lat, "lon": lon, "appid": API_KEY, "units": "metric"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if str(data.get("cod")) != "200":
        raise RuntimeError(data.get("message", "OpenWeatherMap current weather unavailable"))

    weather = data.get("weather", [{}])[0]
    return {
        "temp": data["main"]["temp"],
        "humidity": data["main"]["humidity"],
        "apparent_temp": data["main"]["feels_like"],
        "weather_code": None,
        "condition": weather.get("main", "Unknown"),
        "wind_speed": (data.get("wind", {}).get("speed") or 0) * 3.6,
    }


# ---------------------------------------------------------------------------
# Weather Slash Cog
# ---------------------------------------------------------------------------
class WeatherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="temperature", description="Get the current temperature of a city.")
    async def slash_temperature(self, interaction: discord.Interaction, city: str = "Iloilo"):
        data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
        if data.get("cod") != "404":
            embed = Embed(title=f"Current Temperature in {city}", description=f"**{data['main'].get('temp', 'N/A')}°C**", color=0x00FF00)
        else:
            embed = Embed(title=f"City {city} not found", description="Sorry, the city you requested could not be found.", color=0xFF0000)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="description", description="Get the weather description of a city.")
    async def slash_description(self, interaction: discord.Interaction, city: str = "Iloilo"):
        data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
        if data.get("cod") != "404":
            desc = data['weather'][0].get('description', 'N/A')
            embed = Embed(title=f"Weather Description in {city}", description=f"**{desc.capitalize()}**", color=0x00FF00)
        else:
            embed = Embed(title=f"City {city} not found", description="Sorry, the city you requested could not be found.", color=0xFF0000)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="humidity", description="Get the humidity of a city.")
    async def slash_humidity(self, interaction: discord.Interaction, city: str = "Iloilo"):
        data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
        if data.get("cod") != "404":
            embed = Embed(title=f"Current Humidity in {city}", description=f"**{data['main'].get('humidity', 'N/A')}%**", color=0x00FF00)
        else:
            embed = Embed(title=f"City {city} not found", description="Sorry, the city you requested could not be found.", color=0xFF0000)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pressure", description="Get the atmospheric pressure of a city.")
    async def slash_pressure(self, interaction: discord.Interaction, city: str = "Iloilo"):
        data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
        if data.get("cod") != "404":
            embed = Embed(title=f"Current Pressure in {city}", description=f"**{data['main'].get('pressure', 'N/A')} hPa**", color=0x00FF00)
        else:
            embed = Embed(title=f"City {city} not found", description="Sorry, the city you requested could not be found.", color=0xFF0000)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="windspeed", description="Get the wind speed of a city.")
    async def slash_windspeed(self, interaction: discord.Interaction, city: str = "Iloilo"):
        data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
        if data.get("cod") != "404":
            embed = Embed(title=f"Current Wind Speed in {city}", description=f"**{data['wind'].get('speed', 'N/A')} m/s**", color=0x00FF00)
        else:
            embed = Embed(title=f"City {city} not found", description="Sorry, the city you requested could not be found.", color=0xFF0000)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="use", description="Provides a link to the web application version of this bot.")
    async def slash_use(self, interaction: discord.Interaction):
        embed = Embed(title="Web Application Version", description=f"[Click here to access the app]({WEB_APP_URL})", color=0x0000FF)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="precipitation", description="Get precipitation details for a city.")
    async def slash_precipitation(self, interaction: discord.Interaction, city: str = "Iloilo"):
        data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
        embed = Embed(title=f"Precipitation in {city}", color=0x00FFFF)
        if data.get("cod") != "404":
            rain = data.get('rain')
            snow = data.get('snow')
            pop = data.get('pop')
            if pop is not None:
                embed.add_field(name="Probability", value=f"{pop * 100:.2f}%", inline=False)
            if rain:
                embed.add_field(name="Rain", value=f"{rain.get('1h', 'N/A')} mm in the last hour", inline=False)
            if snow:
                embed.add_field(name="Snow", value=f"{snow.get('1h', 'N/A')} mm in the last hour", inline=False)
            if not rain and not snow and pop is None:
                embed.add_field(name="No Rain or Snow", value="No significant precipitation reported.", inline=False)
        else:
            embed = Embed(title=f"City {city} Not Found", description="We couldn't find weather data for this city.", color=0xFF0000)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="typhoon", description="Get the latest typhoon alert for Iloilo City.")
    async def slash_typhoon(self, interaction: discord.Interaction):
        data = requests.get(f"https://api.openweathermap.org/data/3.0/onecall?lat={ILOILO_COORDS['lat']}&lon={ILOILO_COORDS['lon']}&exclude=minutely,hourly,daily&appid={API_KEY}").json()
        alerts = data.get("alerts", [])
        if alerts:
            for alert in alerts:
                if "typhoon" in alert.get("event", "").lower() or "cyclone" in alert.get("event", "").lower():
                    name = alert.get('event', 'Unknown Typhoon')
                    embed = Embed(title="⚠️ Typhoon Alert for Iloilo City!", color=0xFF4500)
                    embed.add_field(name="Typhoon Name", value=name, inline=False)
                    embed.add_field(name="Description", value=alert.get('description', 'N/A'), inline=False)
                    embed.add_field(name="Start Time", value=_format_datetime_pht(alert.get('start', 0)), inline=False)
                    embed.add_field(name="End Time", value=_format_datetime_pht(alert.get('end', 0)), inline=False)
                    embed.add_field(name="TCWS Status", value=get_tcws_description(0), inline=False)
                    await interaction.response.send_message(embed=embed)
                    return
        embed = Embed(title="✅ No Typhoon Alerts for Iloilo City", description="There are currently no typhoon alerts in effect. Stay safe!", color=0x32CD32)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="air", description="Get the air quality for a specific city.")
    async def slash_air(self, interaction: discord.Interaction, city: str = "Iloilo"):
        data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
        if data.get("cod") != "404":
            lat, lon = data['coord']['lat'], data['coord']['lon']
            aqi_data = requests.get(f"http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}").json()
            aqi = aqi_data['list'][0]['main']['aqi']
            desc = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}.get(aqi, "Unknown")
            comp = aqi_data['list'][0]['components']
            pollutants = (
                f"CO: {comp.get('co', 0)} µg/m³\nNO: {comp.get('no', 0)} µg/m³\n"
                f"NO₂: {comp.get('no2', 0)} µg/m³\nO₃: {comp.get('o3', 0)} µg/m³\n"
                f"SO₂: {comp.get('so2', 0)} µg/m³\nNH₃: {comp.get('nh3', 0)} µg/m³\n"
                f"PM2.5: {comp.get('pm2_5', 0)} µg/m³\nPM10: {comp.get('pm10', 0)} µg/m³"
            )
            embed = Embed(title=f"Air Quality in {city}", color=0x87CEEB)
            embed.add_field(name="Air Quality", value=desc, inline=False)
            embed.add_field(name="Pollutants", value=pollutants, inline=False)
        else:
            embed = Embed(title=f"City {city} Not Found", description="We couldn't find weather data for this city.", color=0xFF0000)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="weather", description="Get current weather, air quality, and precipitation for a city.")
    async def slash_weather(self, interaction: discord.Interaction, city: str = "Iloilo"):
        data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
        if data.get("cod") != "404":
            desc = data['weather'][0]['description']
            temp = data['main']['temp']
            hum = data['main']['humidity']
            wind = data['wind']['speed']
            rain = data.get('rain')
            snow = data.get('snow')
            lat, lon = data['coord']['lat'], data['coord']['lon']
            aqi_data = requests.get(f"http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}").json()
            aqi = aqi_data['list'][0]['main']['aqi'] if 'list' in aqi_data and aqi_data['list'] else None
            aqi_desc = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}.get(aqi, "Unknown")

            embed = Embed(title=f"Current Weather in {city}", color=discord.Color.blue())
            embed.add_field(name="Date", value=_format_datetime_pht(data['dt']))
            embed.add_field(name="Temperature", value=f"{temp}°C")
            embed.add_field(name="Description", value=desc.capitalize())
            embed.add_field(name="Humidity", value=f"{hum}%")
            embed.add_field(name="Wind Speed", value=f"{wind} m/s")
            embed.add_field(name="Air Quality", value=aqi_desc)
            precip = ""
            if rain:
                precip += f"Rain: {rain.get('1h', 'N/A')} mm/hr\n"
            if snow:
                precip += f"Snow: {snow.get('1h', 'N/A')} mm/hr\n"
            embed.add_field(name="Precipitation", value=precip or "No significant rain or snow reported.", inline=False)
        else:
            embed = Embed(title=f"City {city} not found.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="forecast", description="Get a clean 3-day forecast for a city.")
    async def slash_forecast(self, interaction: discord.Interaction, city: str = "Iloilo"):
        await interaction.response.defer()

        days = []
        fallback = False

        # Try Open-Meteo first
        try:
            coords = geocode_city(city)
            if coords:
                days = get_openmeteo_daily_forecast(*coords, days=3)
        except Exception as e:
            print(f"[Forecast Open-Meteo Error] {e}")

        # Fall back to OpenWeatherMap if Open-Meteo failed or returned nothing
        if not days:
            try:
                days = _owm_daily_forecast(city=city, days=3)
                fallback = True
            except Exception as e:
                await interaction.followup.send(
                    embed=Embed(title="❌ Forecast unavailable", description=str(e), color=discord.Color.red())
                )
                return

        if not days:
            await interaction.followup.send(
                embed=Embed(title="❌ Forecast unavailable", description="No forecast data returned.", color=discord.Color.red())
            )
            return

        embeds = [self._build_forecast_embed(city, day) for day in days]
        if fallback and embeds:
            note = "\n\n*Using OpenWeatherMap fallback data*"
            embeds[0].description = (embeds[0].description or "") + note
        await _send_paginated_embeds(interaction, embeds)

    @staticmethod
    def _build_forecast_embed(city: str, day: dict) -> Embed:
        """Build a clean, laid-out embed for one forecast day."""
        condition = day.get("description") or wmo_description(day.get("weather_code"))
        embed = Embed(
            title=f"🌤️ Forecast for {city}",
            description=f"**{_format_date_label(day['date'])}**\n{condition}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="🌡 High", value=f"{day['temp_max']}°C", inline=True)
        embed.add_field(name="🌡 Low", value=f"{day['temp_min']}°C", inline=True)
        embed.add_field(name="🥵 Feels Like High", value=f"{day['feels_like_max']}°C", inline=True)
        embed.add_field(name="🥶 Feels Like Low", value=f"{day['feels_like_min']}°C", inline=True)
        embed.add_field(name="🌧 Rain Chance", value=f"{day['precipitation_probability']}%", inline=True)
        embed.add_field(name="💨 Max Wind", value=f"{day['wind_speed_max']} km/h", inline=True)
        return embed

    @app_commands.command(name="search", description="Search for current weather and air quality in a city.")
    async def slash_search(self, interaction: discord.Interaction, city: str = "Iloilo"):
        data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
        if data.get("cod") != "404":
            desc = data['weather'][0]['description']
            temp = data['main']['temp']
            hum = data['main']['humidity']
            wind = data['wind']['speed']
            lat, lon = data['coord']['lat'], data['coord']['lon']
            aqi_data = requests.get(f"http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}").json()
            aqi = aqi_data['list'][0]['main']['aqi'] if 'list' in aqi_data and aqi_data['list'] else None
            aqi_desc = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}.get(aqi, "Unknown")
            embed = Embed(title=f"Current Weather in {city}", color=discord.Color.blue())
            embed.add_field(name="Date", value=_format_datetime_pht(data['dt']))
            embed.add_field(name="Temperature", value=f"{temp}°C")
            embed.add_field(name="Description", value=desc.capitalize())
            embed.add_field(name="Humidity", value=f"{hum}%")
            embed.add_field(name="Wind Speed", value=f"{wind} m/s")
            embed.add_field(name="Air Quality", value=aqi_desc)
        else:
            embed = Embed(title=f"City {city} not found.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="ping", description="Shows the bot's ping")
    async def slash_ping(self, interaction: discord.Interaction):
        embed = Embed(title="Bot Ping", color=discord.Color.blue())
        embed.add_field(name="Latency", value=f"{round(interaction.client.latency * 1000)}ms")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="class", description="Check the class suspension forecast based on the feels-like temperature.")
    async def class_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        current = None
        daily = []
        fallback = False

        # Try Open-Meteo first
        try:
            current, daily = _get_class_weather_data()
        except Exception as e:
            print(f"[Class Open-Meteo Error] {e}")

        # Fall back to OpenWeatherMap for Iloilo
        if not current or not daily or len(daily) < 3:
            try:
                lat, lon = ILOILO_COORDS["lat"], ILOILO_COORDS["lon"]
                current = _owm_current(lat, lon)
                daily = _owm_daily_forecast(lat=lat, lon=lon, days=3)
                fallback = True
            except Exception as e:
                await interaction.followup.send(
                    embed=Embed(title="⚠ Error fetching weather data", description=str(e), color=discord.Color.red()),
                    ephemeral=True,
                )
                return

        if not current or not daily or len(daily) < 3:
            await interaction.followup.send(
                embed=Embed(title="⚠ Error fetching weather data", description="Try again later.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        forecasts = [
            ("Today", {
                "temp": current["temp"],
                "feels_like": current["apparent_temp"],
                "humidity": current["humidity"],
                "precipitation_probability": None,
                "wind_speed": current["wind_speed"],
                "weather_code": current.get("weather_code"),
                "condition": current.get("condition"),
            }),
            ("Next day", {
                "temp": daily[1]["temp_max"],
                "feels_like": daily[1]["feels_like_max"],
                "humidity": None,
                "precipitation_probability": daily[1]["precipitation_probability"],
                "wind_speed": daily[1]["wind_speed_max"],
                "weather_code": daily[1].get("weather_code"),
                "condition": daily[1].get("description"),
            }),
            ("Next two days", {
                "temp": daily[2]["temp_max"],
                "feels_like": daily[2]["feels_like_max"],
                "humidity": None,
                "precipitation_probability": daily[2]["precipitation_probability"],
                "wind_speed": daily[2]["wind_speed_max"],
                "weather_code": daily[2].get("weather_code"),
                "condition": daily[2].get("description"),
            }),
        ]
        index = 0

        class CView(View):
            def __init__(self, idx):
                super().__init__(timeout=120)
                self.index = idx
                self.update_buttons()

            def update_buttons(self):
                self.children[0].disabled = self.index == 0
                self.children[1].disabled = self.index == len(forecasts) - 1

            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.primary, disabled=True)
            async def prev_button(self, interaction: discord.Interaction, button: Button):
                self.index -= 1
                await update_embed(interaction, self.index)

            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary)
            async def next_button(self, interaction: discord.Interaction, button: Button):
                self.index += 1
                await update_embed(interaction, self.index)

        def make_embed(idx):
            title, data = forecasts[idx]
            embed = _build_class_embed(title, data)
            if fallback and idx == 0:
                embed.description = (embed.description or "") + "\n\n*Using OpenWeatherMap fallback data*"
            return embed

        async def update_embed(interaction, idx):
            view = CView(idx)
            await interaction.response.edit_message(embed=make_embed(idx), view=view)

        await interaction.followup.send(embed=make_embed(index), view=CView(index), ephemeral=True)


async def setup(bot: commands.Bot):
    for command in PREFIX_COMMANDS:
        bot.add_command(command)
    await bot.add_cog(WeatherCog(bot))
