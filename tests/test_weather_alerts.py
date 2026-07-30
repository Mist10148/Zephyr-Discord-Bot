"""What a weather subscription decides to say.

Pure functions over a hand-built Open-Meteo bundle -- no network, no Discord.
These are the same functions the dashboard's preview calls, which is the point:
a preview computed by different code would be a preview of something else.
"""

import pytest

from zephyr.utils import weather_alerts as alerts


def _bundle(*, wind=10, rain=10, feels=25, code=0, feels_max=30, hours=3):
    times = [f"2026-07-30T{9 + index:02d}:00" for index in range(hours)]
    return {
        "current": {"time": "2026-07-30T09:00", "temperature_2m": 28, "apparent_temperature": feels, "weather_code": code},
        "daily": {
            "time": ["2026-07-30"], "weather_code": [code],
            "temperature_2m_max": [33], "temperature_2m_min": [25],
            "apparent_temperature_max": [feels_max], "apparent_temperature_min": [26],
            "precipitation_probability_max": [rain], "wind_speed_10m_max": [wind],
        },
        "hourly": {
            "time": times,
            "wind_speed_10m": [wind] * hours,
            "precipitation_probability": [rain] * hours,
            "apparent_temperature": [feels] * hours,
            "weather_code": [code] * hours,
        },
    }


class TestDailyDigest:
    def test_it_reports_todays_numbers(self):
        digest = alerts.build_daily_digest(_bundle(wind=40, rain=60), location="Iloilo City")

        fields = {field["name"]: field["value"] for field in digest["fields"]}
        assert fields["High / Low"] == "33°C / 25°C"
        assert fields["Chance of rain"] == "60%"
        assert fields["Max wind"] == "40 km/h"
        assert "Iloilo City" in digest["title"]

    def test_a_missing_value_renders_as_a_dash_not_none(self):
        bundle = _bundle()
        bundle["daily"]["wind_speed_10m_max"] = [None]

        digest = alerts.build_daily_digest(bundle, location="X")

        assert {field["name"]: field["value"] for field in digest["fields"]}["Max wind"] == "—"

    def test_imperial_units_change_the_labels(self):
        digest = alerts.build_daily_digest(_bundle(), location="X", units="imperial")
        assert "°F" in {field["name"]: field["value"] for field in digest["fields"]}["High / Low"]

    def test_the_fingerprint_is_per_day_and_place(self):
        one = alerts.build_daily_digest(_bundle(), location="Iloilo City")
        same = alerts.build_daily_digest(_bundle(wind=99), location="Iloilo City")
        elsewhere = alerts.build_daily_digest(_bundle(), location="Manila")

        assert one["fingerprint"] == same["fingerprint"]
        assert one["fingerprint"] != elsewhere["fingerprint"]


class TestSevere:
    def test_calm_weather_says_nothing(self):
        """None is the quiet result: the caller must not post it, and must not
        record it as a run either."""
        assert alerts.evaluate_severe(_bundle(), None, location="X") is None

    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            ({"wind": 80}, "Wind up to 80"),
            ({"rain": 95}, "95% chance of rain"),
            ({"feels": 41}, "Feels like 41"),
            ({"code": 95}, "Thunderstorm"),
        ],
    )
    def test_each_threshold_can_trigger_on_its_own(self, kwargs, expected):
        alert = alerts.evaluate_severe(_bundle(**kwargs), None, location="X")
        assert alert is not None
        assert any(expected in reason for reason in alert["reasons"])

    def test_custom_thresholds_override_the_defaults(self):
        quiet = alerts.evaluate_severe(_bundle(wind=30), None, location="X")
        loud = alerts.evaluate_severe(_bundle(wind=30), {"wind_speed": 25}, location="X")

        assert quiet is None
        assert loud is not None

    def test_a_threshold_can_be_switched_off(self):
        assert alerts.evaluate_severe(_bundle(code=95), {"storm": False}, location="X") is None

    def test_the_same_storm_keeps_the_same_fingerprint(self):
        """The watcher runs four times an hour; without this the channel gets
        four identical warnings until the weather changes."""
        first = alerts.evaluate_severe(_bundle(wind=82), None, location="X")
        again = alerts.evaluate_severe(_bundle(wind=84), None, location="X")

        assert first["fingerprint"] == again["fingerprint"]

    def test_a_real_escalation_is_a_new_fingerprint(self):
        moderate = alerts.evaluate_severe(_bundle(wind=65), None, location="X")
        worse = alerts.evaluate_severe(_bundle(wind=110), None, location="X")

        assert moderate["fingerprint"] != worse["fingerprint"]

    def test_a_bundle_with_no_hours_is_quiet_rather_than_an_error(self):
        assert alerts.evaluate_severe({"current": {}, "hourly": {}}, None, location="X") is None


class TestClassSuspension:
    def test_a_mild_day_says_nothing(self):
        assert alerts.evaluate_class_suspension(_bundle(feels_max=30), location="X") is None

    @pytest.mark.parametrize("feels_max,level", [(39, "possible"), (42, "likely"), (51, "certain")])
    def test_the_level_follows_the_heat_index(self, feels_max, level):
        alert = alerts.evaluate_class_suspension(_bundle(feels_max=feels_max), location="X")
        assert alert["level"] == level

    def test_it_says_it_is_advisory(self):
        """It is a heat-index reading, not an announcement from any authority."""
        alert = alerts.evaluate_class_suspension(_bundle(feels_max=42), location="X")
        assert any("Advisory only" in field["name"] for field in alert["fields"])

    def test_a_steady_forecast_does_not_repeat_but_an_escalation_does(self):
        steady = alerts.evaluate_class_suspension(_bundle(feels_max=42), location="X")
        same = alerts.evaluate_class_suspension(_bundle(feels_max=43), location="X")
        worse = alerts.evaluate_class_suspension(_bundle(feels_max=51), location="X")

        assert steady["fingerprint"] == same["fingerprint"]
        assert steady["fingerprint"] != worse["fingerprint"]


class TestDispatch:
    @pytest.mark.parametrize("kind", ["daily", "severe", "class_suspension"])
    def test_every_stored_kind_is_dispatchable(self, kind):
        from zephyr.db.weather_subs import KINDS

        assert kind in KINDS
        # A stormy bundle so all three have something to say.
        assert alerts.evaluate(kind, _bundle(wind=90, rain=95, feels=42, code=95, feels_max=45), location="X") is not None

    def test_an_unknown_kind_is_quiet_rather_than_fatal(self):
        assert alerts.evaluate("hurricane", _bundle(), location="X") is None
