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
