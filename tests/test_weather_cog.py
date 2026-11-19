"""The weather cog's HTTP plumbing, error handling and day grouping."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import requests

from zephyr.cogs import weather as cog
from zephyr.utils.weather_utils import tcws_from_alert


class TestOk:
    @pytest.mark.parametrize("cod", [200, "200"])
    def test_success_either_type(self, cod):
        assert cog._ok({"cod": cod})

    @pytest.mark.parametrize("cod", [401, "404", 429, "error", None])
    def test_errors_are_not_weather_data(self, cod):
        """cod != "404" used to treat a bad key or a rate limit as data."""
        assert not cog._ok({"cod": cod})

    def test_failure_text_distinguishes_not_found_from_outage(self):
        assert cog._failure({"cod": "404"}, "Atlantis") == "City Atlantis not found."
        assert "Invalid API key" in cog._failure({"cod": 401, "message": "Invalid API key"}, "Iloilo")


class TestGetJson:
    def test_a_network_error_never_leaks_the_url(self, monkeypatch, capsys):
        def boom(url, timeout):
            raise requests.ConnectionError(f"failed to reach {url}")

        monkeypatch.setattr(cog.requests, "get", boom)
        data = asyncio.run(cog._get_json("https://api.example/x?appid=SECRET"))

        assert data["cod"] == "error"
        assert "SECRET" not in data["message"]
        assert "SECRET" not in capsys.readouterr().out

    def test_a_timeout_is_always_passed(self, monkeypatch):
        seen = {}

        def fake_get(url, timeout):
            seen["timeout"] = timeout
            return MagicMock(json=lambda: {"cod": 200})

        monkeypatch.setattr(cog.requests, "get", fake_get)
        assert asyncio.run(cog._get_json("https://x")) == {"cod": 200}
        assert seen["timeout"] == cog.HTTP_TIMEOUT_SECONDS


class TestTcws:
    def test_the_signal_is_read_from_the_bulletin(self):
        alert = {"event": "Typhoon Warning", "description": "Signal No. 1 raised; Signal No. 3 over Iloilo."}
        assert tcws_from_alert(alert) == "TCWS Level 3: Destructive Typhoon"

    def test_tcws_hash_form(self):
        assert tcws_from_alert({"description": "TCWS #2 in effect"}) == "TCWS Level 2: Threatening Typhoon"

    def test_no_signal_mentioned_is_said_honestly(self):
        assert "not stated" in tcws_from_alert({"event": "Typhoon", "description": "Heavy rain expected."})


class TestOwmDayGrouping:
    def test_days_follow_the_places_own_calendar(self, monkeypatch):
        """Iloilo is UTC+8: 23:00 UTC is already the next local day."""
        pht = timezone(timedelta(hours=8))
        local_today = datetime.now(pht).date()
        late_utc = datetime.combine(local_today, datetime.min.time(), pht) + timedelta(hours=2)  # 02:00 local
        payload = {
            "cod": "200",
            "city": {"timezone": 8 * 3600},
            "list": [{"dt": int(late_utc.timestamp()), "main": {"temp": 30, "feels_like": 35},
                      "wind": {"speed": 1}, "pop": 0.5, "weather": [{"main": "Rain"}]}],
        }
        response = MagicMock(json=lambda: payload)
        monkeypatch.setattr(cog.requests, "get", lambda *a, **k: response)

        days = cog._owm_daily_forecast(lat=10.7, lon=122.5, days=1)

        assert [day["date"] for day in days] == [local_today.strftime("%Y-%m-%d")]


class TestNoKeyLeaks:
    def test_owm_errors_never_include_the_request_url(self, monkeypatch):
        response = MagicMock(status_code=401, json=lambda: {"cod": 401, "message": "Invalid API key"})
        monkeypatch.setattr(cog.requests, "get", lambda *a, **k: response)
        monkeypatch.setattr(cog, "API_KEY", "SECRET")
        with pytest.raises(cog.WeatherFetchError) as info:
            cog._owm_daily_forecast(city="Iloilo", days=1)
        assert "SECRET" not in str(info.value)
        assert "appid" not in str(info.value)

    def test_city_names_are_url_encoded(self, monkeypatch):
        monkeypatch.setattr(cog, "API_KEY", "k")
        url = cog._current_url("A&B #1?")
        assert url.startswith("https://")
        assert "q=A%26B+%231%3F" in url

    def test_a_day_of_empty_slots_is_skipped_not_a_crash(self, monkeypatch):
        payload = {"cod": "200", "city": {"timezone": 0},
                   "list": [{"dt": int(datetime.now(timezone.utc).timestamp()), "main": {"temp": None},
                             "wind": {}, "pop": 0, "weather": []}]}
        monkeypatch.setattr(cog.requests, "get", lambda *a, **k: MagicMock(status_code=200, json=lambda: payload))
        assert cog._owm_daily_forecast(lat=0, lon=0, days=1) == []


def test_class_and_subscription_share_one_scale():
    from zephyr.utils.weather_utils import class_suspension_payload

    for feels in (14, 15, 16, 37.9, 38, 41, 50):
        level = class_suspension_payload(feels)["level"]
        decision, _ = cog.class_suspension(feels)
        assert decision == cog._SUSPENSION_DECISIONS[level]


class TestSavedLocation:
    """/setlocation stored coordinates nothing read; the name alone was re-geocoded."""

    def test_the_saved_coordinates_are_used(self, monkeypatch):
        monkeypatch.setattr(cog, "read_bot_user", lambda user_id: {
            "default_city": "San Jose", "lat": 37.34, "lon": -121.89})
        instance = cog.WeatherCog.__new__(cog.WeatherCog)
        interaction = MagicMock()
        interaction.user.id = 1

        city = asyncio.run(instance._city_for(interaction, None))

        assert city == "San Jose"
        url = cog._current_url(city)
        assert "lat=37.34" in url and "lon=-121.89" in url and "q=" not in url

    def test_a_typed_city_is_still_looked_up_by_name(self):
        assert "q=Manila" in cog._current_url("Manila")

    def test_the_forecast_skips_re_geocoding(self, monkeypatch):
        seen = {}

        def forecast(lat, lon, days=3):
            seen["coords"] = (lat, lon)
            return [{"date": "2026-11-18", "weather_code": 0, "temp_max": 20, "temp_min": 10,
                     "feels_like_max": 20, "feels_like_min": 10, "precipitation_probability": 0,
                     "wind_speed_max": 5}]

        def no_geocode(*args, **kwargs):
            raise AssertionError("re-geocoded a saved place")

        monkeypatch.setattr(cog, "_resolve_place", no_geocode)
        monkeypatch.setattr(cog, "get_openmeteo_daily_forecast", forecast)
        pages = asyncio.run(cog._forecast_pages(cog.SavedPlace("San Jose", 37.34, -121.89)))

        assert seen["coords"] == (37.34, -121.89)
        assert pages


def test_web_forecast_times_follow_dst_per_row():
    """One utc_offset_seconds (today's) applied to all 7 days put rows after a
    DST change an hour off."""
    from website.api.weather import _time_payload, _zone

    zone = _zone({"timezone": "America/New_York"}, -4 * 3600)
    before = _time_payload("2026-10-31T12:00", zone)["time_epoch"]   # EDT, UTC-4
    after = _time_payload("2026-11-02T12:00", zone)["time_epoch"]    # EST, UTC-5
    assert before == int(datetime(2026, 10, 31, 16, tzinfo=timezone.utc).timestamp())
    assert after == int(datetime(2026, 11, 2, 17, tzinfo=timezone.utc).timestamp())
    # An unknown zone falls back to the fixed offset.
    fixed = _zone({"timezone": "Not/AZone"}, 3600)
    assert _time_payload("2026-01-01T00:00", fixed)["time_epoch"] == int(
        datetime(2025, 12, 31, 23, tzinfo=timezone.utc).timestamp())


def test_the_forecast_fallback_keeps_the_saved_coordinates(monkeypatch):
    seen = {}

    def owm(city=None, lat=None, lon=None, days=3):
        seen.update(city=city, lat=lat, lon=lon)
        return [{"date": "2026-11-18", "weather_code": 0, "temp_max": 20, "temp_min": 10,
                 "feels_like_max": 20, "feels_like_min": 10, "precipitation_probability": 0,
                 "wind_speed_max": 5}]

    def open_meteo_down(*args, **kwargs):
        raise RuntimeError("Open-Meteo is down")

    monkeypatch.setattr(cog, "get_openmeteo_daily_forecast", open_meteo_down)
    monkeypatch.setattr(cog, "_owm_daily_forecast", owm)
    asyncio.run(cog._forecast_pages(cog.SavedPlace("San Jose", 37.34, -121.89)))

    assert seen == {"city": None, "lat": 37.34, "lon": -121.89}


def test_class_today_is_rated_on_the_days_peak(monkeypatch):
    """At 06:00 the current feels-like is mild while the forecast peak is not."""
    current = {"temp": 26, "apparent_temp": 27, "humidity": 80, "wind_speed": 5, "weather_code": 1}
    day = {"temp_max": 33, "feels_like_max": 42, "precipitation_probability": 10, "wind_speed_max": 10,
           "weather_code": 1}
    monkeypatch.setattr(cog, "_get_class_weather_data", lambda: (current, [day, day, day]))
    captured = {}

    async def send(interaction, embeds, ephemeral=False):
        captured["embeds"] = embeds

    monkeypatch.setattr(cog, "_send_paginated_embeds", send)
    interaction = MagicMock()

    async def run():
        interaction.response.defer = MagicMock(return_value=asyncio.sleep(0))
        await cog.WeatherCog.class_command.callback(cog.WeatherCog.__new__(cog.WeatherCog), interaction)

    asyncio.run(run())
    today = captured["embeds"][0]
    assert "42" in today.description


def test_snow_is_not_rain():
    from zephyr.utils.weather_utils import wmo_emoji
    assert wmo_emoji(73) == "🌨️"
    assert wmo_emoji(61) == "🌧️"
    assert wmo_emoji(95) == "⛈️"


def test_the_dashboard_class_outlook_uses_todays_peak(client, monkeypatch):
    import website.api.weather as web

    bundle = {"timezone": "Asia/Manila", "utc_offset_seconds": 28800,
              "current": {"time": "2026-11-19T06:00", "apparent_temperature": 27},
              "hourly": {}, "daily": {"time": ["2026-11-19"], "apparent_temperature_max": [42]}}
    monkeypatch.setattr(web, "get_openmeteo_bundle", lambda *a, **k: bundle)
    monkeypatch.setattr(web, "get_openmeteo_air_quality", lambda *a, **k: {"current": {}})
    web.cache.clear() if hasattr(web.cache, "clear") else None

    payload = client.get("/api/v1/weather?lat=10.72&lon=122.56").get_json()

    assert payload["class_suspension"]["level"] == "likely"
