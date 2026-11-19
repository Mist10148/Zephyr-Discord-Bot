"""The weather embed designs: complete when data is there, safe when it is not."""

import discord
import pytest

from zephyr.cogs.weather_alerts import alert_embed
from zephyr.utils import weather_embeds as look

OWM_CURRENT = {
    "cod": 200,
    "name": "Iloilo City",
    "dt": 1_763_442_000,
    "timezone": 8 * 3600,
    "coord": {"lat": 10.72, "lon": 122.56},
    "sys": {"country": "PH", "sunrise": 1_763_416_800, "sunset": 1_763_458_200},
    "weather": [{"id": 803, "main": "Clouds", "description": "broken clouds", "icon": "04d"}],
    "main": {"temp": 30.6, "feels_like": 36.2, "temp_min": 29.1, "temp_max": 31.4, "pressure": 1009, "humidity": 74},
    "wind": {"speed": 4.1, "deg": 45, "gust": 6.2},
    "clouds": {"all": 75},
    "visibility": 10000,
    "rain": {"1h": 0.42},
}


def _valid(embed: discord.Embed) -> None:
    assert len(embed) <= 6000
    assert embed.title is None or len(embed.title) <= 256
    assert embed.description is None or len(embed.description) <= 4096
    assert len(embed.fields) <= 25
    for field in embed.fields:
        assert 0 < len(field.name) <= 256 and 0 < len(field.value) <= 1024


def test_the_current_card_shows_the_full_picture():
    embed = look.current_embed(OWM_CURRENT, aqi=2)
    _valid(embed)
    assert embed.author.name == "📍 Iloilo City, PH"
    assert "31°C" in embed.description and "Feels like **36°C**" in embed.description
    assert "0.42 mm" in embed.description
    fields = {field.name: field.value for field in embed.fields}
    assert fields["💨 Wind"].startswith("15 km/h NE")
    assert "Gusts 22 km/h" in fields["💨 Wind"]
    assert fields["🌅 Sunrise"] == "6:00 AM"          # in the place's own time zone
    assert "Fair" in fields["🍃 Air quality"]
    assert embed.thumbnail.url.endswith("04d@4x.png")
    assert embed.color.value == look.COLORS["clouds"]
    assert "OpenWeatherMap" in embed.footer.text


@pytest.mark.parametrize("payload", [
    {"cod": 200},
    {"cod": 200, "name": "X", "main": {}, "wind": {}, "weather": []},
    {"cod": 200, "main": {"temp": None}, "sys": None, "clouds": None},
])
def test_partial_responses_never_crash(payload):
    _valid(look.current_embed(payload, fallback_city="Somewhere"))
    for metric in look.METRICS:
        _valid(look.metric_embed(payload, metric, fallback_city="Somewhere"))
    _valid(look.air_embed(payload, None, fallback_city="Somewhere"))


def test_every_metric_card_renders():
    for metric in look.METRICS:
        embed = look.metric_embed(OWM_CURRENT, metric)
        _valid(embed)
        assert embed.author.name.startswith("📍")


def test_missing_pollutants_read_na_not_zero():
    entry = {"main": {"aqi": 4}, "components": {"pm2_5": 55.2}}
    embed = look.air_embed(OWM_CURRENT, entry)
    _valid(embed)
    fields = {field.name: field.value for field in embed.fields}
    assert fields["PM2.5"] == "`55.2` µg/m³"
    assert fields["CO"] == "N/A"
    assert embed.color.value == look.AQI_STYLE[4][1]


def test_forecast_has_an_overview_and_a_page_per_day():
    days = [
        {"date": "2026-11-18", "weather_code": 61, "temp_max": 31.2, "temp_min": 24.9, "feels_like_max": 37,
         "feels_like_min": 26, "precipitation_probability": 80, "wind_speed_max": 21.4},
        {"date": "2026-11-19", "weather_code": None, "description": "Clear", "temp_max": None, "temp_min": None,
         "feels_like_max": None, "feels_like_min": None, "precipitation_probability": None, "wind_speed_max": None},
    ]
    pages = look.forecast_embeds("Iloilo City, PH", days, fallback=True)
    assert len(pages) == 3
    for page in pages:
        _valid(page)
    assert "fallback" in pages[0].footer.text
    assert pages[1].color.value == look.COLORS["rain"]
    assert pages[2].color.value == look.COLORS["clear"]


@pytest.mark.parametrize("feels, level", [(52, "certain"), (42, "likely"), (39, "possible"), (30, "none")])
def test_class_outlook_matches_the_shared_scale(feels, level):
    embed = look.class_embed("Today", {"temp": 31, "feels_like": feels, "humidity": 70,
                                       "wind_speed": 12, "condition": "Clouds"})
    _valid(embed)
    assert look.SUSPENSION_STYLE[level][1] in embed.description
    assert "Advisory only" in embed.footer.text


def test_a_long_typhoon_bulletin_fits():
    alert = {"event": "Typhoon Warning", "description": "Signal No. 3 " + "x" * 6000, "start": 1, "end": None}
    embed = look.typhoon_embed(alert, tcws="TCWS Level 3: Destructive Typhoon")
    _valid(embed)
    fields = {field.name: field.value for field in embed.fields}
    assert fields["🕘 Until"] == look.DASH


def test_daily_digest_shows_the_class_outlook_it_computed():
    alert = {"kind": "daily", "title": "☀️ Today in Iloilo City", "summary": "Clear sky",
             "fields": [{"name": "High / Low", "value": "33°C / 25°C"}],
             "class_suspension": {"level": "likely", "reason": "Extreme Heat"}}
    embed = alert_embed(alert)
    _valid(embed)
    names = [field.name for field in embed.fields]
    assert "🌡️ High / Low" in names and "🏫 Classes" in names


def test_the_advisory_note_moves_to_the_footer():
    alert = {"kind": "class_suspension", "title": "🌡️ Class suspension watch — X", "summary": "s",
             "fields": [{"name": "Feels like (max)", "value": "42°C"},
                        {"name": "Advisory only", "value": "Based on the heat index."}]}
    embed = alert_embed(alert)
    assert "Advisory only" in embed.footer.text
    assert all("Advisory" not in field.name for field in embed.fields)


@pytest.mark.asyncio
async def test_the_pager_keeps_footers_and_belongs_to_its_owner():
    from unittest.mock import AsyncMock, MagicMock

    from zephyr.utils.pagination import EmbedPager

    pages = [discord.Embed(title="a").set_footer(text="Zephyr Weather"), discord.Embed(title="b")]
    view = EmbedPager(pages, owner_id=1)
    assert pages[0].footer.text == "Zephyr Weather • Page 1/2"
    assert pages[1].footer.text == "Page 2/2"
    assert view.previous.disabled and not view.next.disabled

    stranger = MagicMock()
    stranger.user.id = 2
    stranger.response.send_message = AsyncMock()
    assert not await view.interaction_check(stranger)
    stranger.response.send_message.assert_awaited_once()

    owner = MagicMock()
    owner.user.id = 1
    owner.response.edit_message = AsyncMock()
    await view._turn(owner, 1)
    assert view.index == 1 and view.next.disabled


def test_current_cards_do_not_call_the_area_spread_a_daily_high_low():
    """OpenWeatherMap's current temp_max/temp_min are not the day's high and low."""
    from zephyr.utils import weather_embeds as look

    data = {"name": "X", "main": {"temp": 30, "feels_like": 35, "temp_min": 29.1, "temp_max": 31.4,
                                  "pressure": 1009, "humidity": 70},
            "weather": [{"id": 800, "main": "Clear", "description": "clear sky"}], "wind": {"speed": 2},
            "timezone": 28800, "dt": 1700000000, "sys": {}}
    assert "↑" not in look.current_embed(data).description
    assert "↑" not in str(look.metric_embed(data, "temperature").to_dict())
