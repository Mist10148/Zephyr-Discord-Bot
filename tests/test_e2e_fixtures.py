"""The Playwright weather fixture must match what `/weather` actually returns.

This guard exists because of a mistake worth keeping a record of. The fixture
was hand-written first, guessing `daily[].date` where the endpoint sends
`time_local`. The E2E spec then clicked a place, the render threw on the missing
key, the app fell into its error boundary, and Playwright reported "heading not
found" — a crash presented as a missing element, three layers from the cause.

A fixture that diverges from the response makes the end-to-end test a test of
the fixture. Key sets are compared rather than values: the values are arbitrary
sample data and *should* be free to change, while a key set that drifts is
always a bug in one of the two.
"""

import json
import pathlib
from unittest.mock import patch

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "website" / "frontend" / "e2e" / "fixtures" / "weather.json"

HOURS = [f"2026-09-{5 + index // 24:02d}T{index % 24:02d}:00" for index in range(48)]
DAYS = [f"2026-09-{5 + index:02d}" for index in range(7)]

BUNDLE = {
    "timezone": "Asia/Manila",
    "utc_offset_seconds": 28800,
    "current": {
        "time": "2026-09-05T09:00",
        "temperature_2m": 31.2,
        "apparent_temperature": 38.4,
        "relative_humidity_2m": 74,
        "wind_speed_10m": 12.6,
        "precipitation": 0.0,
        "weather_code": 2,
    },
    "hourly": {
        "time": HOURS,
        "temperature_2m": [30 + (index % 5) for index in range(48)],
        "apparent_temperature": [36 + (index % 5) for index in range(48)],
        "precipitation_probability": [(index * 7) % 100 for index in range(48)],
        "weather_code": [2 if index % 3 else 61 for index in range(48)],
        "wind_speed_10m": [10 + (index % 8) for index in range(48)],
    },
    "daily": {
        "time": DAYS,
        "weather_code": [61, 2, 3, 61, 80, 2, 1],
        "temperature_2m_max": [33, 32, 34, 31, 33, 32, 33],
        "temperature_2m_min": [25, 25, 26, 24, 25, 25, 26],
        "apparent_temperature_max": [40, 39, 41, 38, 40, 39, 40],
        "apparent_temperature_min": [27, 27, 28, 26, 27, 27, 28],
        "precipitation_probability_max": [60, 20, 10, 70, 90, 20, 5],
        "wind_speed_10m_max": [22, 18, 16, 25, 30, 18, 14],
    },
}

AIR_QUALITY = {
    "current": {
        "european_aqi": 32,
        "us_aqi": 41,
        "pm10": 18.2,
        "pm2_5": 9.4,
        "ozone": 44.0,
        "nitrogen_dioxide": 6.1,
    }
}


@pytest.fixture
def live(client):
    """The endpoint's real response, with the provider stubbed.

    Stubbed rather than called: the guard must not depend on Open-Meteo being
    up, which is the same reason the E2E suite stubs it in the browser.
    """
    with patch("website.api.weather.get_openmeteo_bundle", return_value=BUNDLE), patch(
        "website.api.weather.get_openmeteo_air_quality", return_value=AIR_QUALITY
    ):
        response = client.get("/api/v1/weather?lat=10.72&lon=122.56&units=metric")
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


@pytest.fixture
def stored():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestTheFixtureMatchesTheEndpoint:
    def test_the_fixture_exists(self):
        """`e2e/fixtures.ts` reads it at runtime, so a missing file is a suite
        that fails on import with no explanation."""
        assert FIXTURE.exists(), f"{FIXTURE} is missing"

    def test_the_top_level_keys_match(self, live, stored):
        assert sorted(stored) == sorted(live)

    def test_the_current_conditions_keys_match(self, live, stored):
        assert sorted(stored["current"]) == sorted(live["current"])

    def test_the_daily_row_keys_match(self, live, stored):
        """`daily[].date` versus `daily[].time_local` is the exact divergence
        that sent the app into its error boundary."""
        assert stored["daily"], "the fixture has no daily rows to compare"
        assert sorted(stored["daily"][0]) == sorted(live["daily"][0])

    def test_the_hourly_row_keys_match(self, live, stored):
        assert stored["hourly"], "the fixture has no hourly rows to compare"
        assert sorted(stored["hourly"][0]) == sorted(live["hourly"][0])

    def test_the_air_quality_keys_match(self, live, stored):
        assert sorted(stored["air_quality"] or {}) == sorted(live["air_quality"] or {})

    def test_the_class_suspension_keys_match(self, live, stored):
        assert sorted(stored["class_suspension"] or {}) == sorted(
            live["class_suspension"] or {}
        )


class TestTheFixtureIsUsable:
    def test_it_has_enough_rows_for_the_screen(self, stored):
        """The hourly strip renders 24 and the week renders 7. A fixture with
        one of each passes every key check and renders an empty week."""
        assert len(stored["hourly"]) >= 24
        assert len(stored["daily"]) >= 7

    def test_the_values_the_specs_assert_on_are_present(self, stored):
        """`public.spec.ts` reads the description and the temperature out of
        this file rather than writing them out, so they have to be there."""
        assert stored["current"]["description"]
        assert isinstance(stored["current"]["temperature"], (int, float))
