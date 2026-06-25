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
from zephyr.utils.weather_utils import get_tcws_description, _format_datetime_pht
from zephyr.utils.help_data import categories_by_key, _send_categorized_help


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
def class_suspension(heat_index):
    if heat_index >= 50:
        return "🔴 **Class will be suspended certainly!**", "**Excessive Heat** 🔴"
    elif heat_index >= 41:
        return "🟠 **High possibility**", "**Very Hot** 🟠"
    elif heat_index >= 38:
        return "🟡 **Low possibility**", "**Hot Conditions** 🟡"
    elif heat_index >= 15:
        return "✅ **Classes will certainly resume**", "**Pleasant Weather** 🟢"
    else:
        return "🤔 **Do those temperature readings even exist?**", "**Unusual Temperature** ⚠"


def get_current_weather():
    response = requests.get(f"{CURRENT_URL}?lat={ILOILO_COORDS['lat']}&lon={ILOILO_COORDS['lon']}&appid={API_KEY}&units=metric")
    if response.status_code != 200:
        return None
    data = response.json()
    return {"temp": data["main"]["temp"], "humidity": data["main"]["humidity"], "heat_index": data["main"]["feels_like"]}


def get_2pm_forecasts():
    response = requests.get(f"{FORECAST_URL}?lat={ILOILO_COORDS['lat']}&lon={ILOILO_COORDS['lon']}&appid={API_KEY}&units=metric")
    if response.status_code != 200:
        return None
    data = response.json()
    today = datetime.utcnow().date()
    next_days = [today + timedelta(days=1), today + timedelta(days=2)]
    forecasts = {}
    for forecast_item in data["list"]:
        timestamp = datetime.utcfromtimestamp(forecast_item["dt"])
        date = timestamp.date()
        hour = timestamp.hour
        if date in next_days and hour == 6:
            forecasts[date] = {
                "temp": forecast_item["main"]["temp"],
                "humidity": forecast_item["main"]["humidity"],
                "heat_index": forecast_item["main"]["feels_like"]
            }
    return [
        ("Next day", forecasts.get(next_days[0], {"temp": 0, "humidity": 0, "heat_index": 0})),
        ("Next two days", forecasts.get(next_days[1], {"temp": 0, "humidity": 0, "heat_index": 0}))
    ]


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

    @app_commands.command(name="helpweather", description="Show a list of available weather commands.")
    async def slash_helpweather(self, interaction: discord.Interaction):
        await _send_categorized_help(
            interaction,
            categories_by_key("weather"),
            title="Weather Help",
            color=discord.Color.blue(),
        )

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

    @app_commands.command(name="forecast", description="Get a 3-day weather and air quality forecast for a city.")
    async def slash_forecast(self, interaction: discord.Interaction, city: str = "Iloilo"):
        data = requests.get(f"{FORECAST_URL}?appid={API_KEY}&q={city}&units=metric").json()
        if data.get("cod") == "200":
            lat, lon = data['city']['coord']['lat'], data['city']['coord']['lon']
            aqi_data = requests.get(f"http://api.openweathermap.org/data/2.5/air_pollution/forecast?lat={lat}&lon={lon}&appid={API_KEY}").json()
            forecasts = data['list'][:24][::2]
            await self._send_forecast_pagination(interaction, city, forecasts, aqi_data.get('list', []))
        else:
            await interaction.response.send_message(embed=Embed(title=f"City {city} not found.", color=0xFF0000))

    async def _send_forecast_pagination(self, interaction, city, forecasts, aqi_list):
        class FView(View):
            def __init__(self, items, aqi_items):
                super().__init__(timeout=120)
                self.items = items
                self.aqi_items = aqi_items
                self.index = 0
                self.prev = Button(label="◀ Previous", style=discord.ButtonStyle.primary, disabled=True)
                self.next = Button(label="Next ▶", style=discord.ButtonStyle.primary, disabled=len(items) <= 1)
                self.prev.callback = self.prev_cb
                self.next.callback = self.next_cb
                self.add_item(self.prev)
                self.add_item(self.next)

            def make_embed(self):
                f = self.items[self.index]
                date = _format_datetime_pht(f['dt'])
                temp = f['main'].get('temp', 'N/A')
                desc = f['weather'][0].get('description', 'N/A')
                hum = f['main'].get('humidity', 'N/A')
                wind = f['wind'].get('speed', 'N/A')
                closest = min(self.aqi_items, key=lambda x: abs(x['dt'] - f['dt']), default=None) if self.aqi_items else None
                aqi = closest['main']['aqi'] if closest else None
                aqi_desc = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}.get(aqi, "Unknown")
                embed = Embed(title=f"Weather Forecast for {city}", color=0x1E90FF)
                embed.add_field(name=f"Forecast for {date}:", value=(
                    f"**Temperature:** {temp}°C\n"
                    f"**Description:** {desc.capitalize()}\n"
                    f"**Humidity:** {hum}%\n"
                    f"**Wind Speed:** {wind} m/s\n"
                    f"**Air Quality:** {aqi_desc}"
                ), inline=False)
                embed.set_footer(text=f"Page {self.index + 1}/{len(self.items)}")
                return embed

            async def prev_cb(self, interaction: discord.Interaction):
                self.index -= 1
                self.prev.disabled = self.index == 0
                self.next.disabled = self.index == len(self.items) - 1
                await interaction.response.edit_message(embed=self.make_embed(), view=self)

            async def next_cb(self, interaction: discord.Interaction):
                self.index += 1
                self.prev.disabled = self.index == 0
                self.next.disabled = self.index == len(self.items) - 1
                await interaction.response.edit_message(embed=self.make_embed(), view=self)

        view = FView(forecasts, aqi_list[:len(forecasts)])
        await interaction.response.send_message(embed=view.make_embed(), view=view)

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

    @app_commands.command(name="class", description="Check the class suspension forecast based on the heat index.")
    async def class_command(self, interaction: discord.Interaction):
        current = get_current_weather()
        forecast_data = get_2pm_forecasts()
        if not current or not forecast_data:
            await interaction.response.send_message("⚠ Error fetching weather data. Try again later.", ephemeral=True)
            return

        forecasts = [("Today", current)] + forecast_data
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
            title, weather_data = forecasts[idx]
            temp, humidity_val, heat = weather_data["temp"], weather_data["humidity"], weather_data["heat_index"]
            suspension, desc = class_suspension(heat)
            return Embed(
                title=f"📅 Class Suspension Forecast: {title}",
                description=(
                    f"🌡 **Temperature:** {temp}°C\n"
                    f"💧 **Humidity:** {humidity_val}%\n"
                    f"🔥 **Heat Index:** {heat}°C\n\n"
                    f"{desc}\n📢 {suspension}"
                ),
                color=discord.Color.blue()
            )

        async def update_embed(interaction, idx):
            view = CView(idx)
            await interaction.response.edit_message(embed=make_embed(idx), view=view)

        await interaction.response.send_message(embed=make_embed(index), view=CView(index))


async def setup(bot: commands.Bot):
    for command in PREFIX_COMMANDS:
        bot.add_command(command)
    await bot.add_cog(WeatherCog(bot))
