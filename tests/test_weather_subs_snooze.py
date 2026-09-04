"""Snoozing a subscription, and sending one on demand.

`enabled=False` was the only way to stop a subscription, and it is a decision to
*stop* -- somebody going away for a week wants the settings and the schedule
kept and only wants quiet meanwhile.

And `/weather-preview` showed the caller privately what a subscription *would*
say, which answers "is this configured right" but not "can the bot actually post
in that channel".
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from zephyr.cogs.weather_alerts import MAX_SNOOZE_SECONDS, _parse_duration
from zephyr.db import weather_subs as repo


def _values(**overrides):
    values = {
        "guild_id": "1", "channel_id": "10", "kind": "daily", "location": "Iloilo City",
        "lat": 10.7, "lon": 122.5, "units": "metric",
        "schedule_local_time": "08:00", "tz": "UTC", "enabled": True,
    }
    values.update(overrides)
    return values


class TestParsingADuration:
    @pytest.mark.parametrize("text,seconds", [
        ("30m", 1800), ("2h", 7200), ("3d", 259200), ("1w", 604800),
        ("2H", 7200), (" 2h ", 7200),
    ])
    def test_it_reads_the_shorthand(self, text, seconds):
        assert _parse_duration(text) == seconds

    def test_a_bare_number_is_hours(self):
        """The unit people mean when they omit one."""
        assert _parse_duration("6") == 6 * 3600

    @pytest.mark.parametrize("text", ["0", "off", "none", "clear"])
    def test_zero_and_its_synonyms_mean_unmute(self, text):
        assert _parse_duration(text) == 0

    @pytest.mark.parametrize("text", ["", "soon", "2 years", "h", "2x", "-3d"])
    def test_nonsense_is_refused_rather_than_guessed(self, text):
        assert _parse_duration(text) is None

    def test_it_is_capped(self):
        """A snooze longer than two years is a delete somebody has not admitted
        to, and unbounded means one typo mutes a subscription forever."""
        assert _parse_duration("9999w") == MAX_SNOOZE_SECONDS


class TestTheRepo:
    def test_a_snoozed_daily_digest_is_not_claimed(self, db_url):
        created = repo.create(_values(), database_url=db_url)
        future = datetime.now(timezone.utc) + timedelta(days=3)
        repo.snooze(created["id"], future, database_url=db_url)

        # Two days on, well past 08:00, and still quiet.
        assert repo.claim_due(datetime.now(timezone.utc) + timedelta(days=2), database_url=db_url) == []

    def test_it_resumes_on_its_normal_schedule(self, db_url):
        """Skipped without being claimed, so `last_run_at` is not advanced --
        otherwise the row would look overdue and fire once immediately on
        waking, which is not what a snooze means."""
        created = repo.create(_values(), database_url=db_url)
        repo.snooze(created["id"], datetime.now(timezone.utc) + timedelta(hours=1), database_url=db_url)
        repo.claim_due(datetime.now(timezone.utc), database_url=db_url)

        assert repo.get(created["id"], database_url=db_url)["last_run_at"] is None

    def test_an_expired_snooze_stops_blocking(self, db_url):
        created = repo.create(_values(), database_url=db_url)
        repo.snooze(created["id"], datetime.now(timezone.utc) - timedelta(minutes=1), database_url=db_url)

        due = repo.claim_due(datetime.now(timezone.utc), database_url=db_url)
        assert [row["id"] for row in due] == [created["id"]]

    def test_a_snoozed_watch_is_not_listed(self, db_url):
        created = repo.create(_values(kind="severe", schedule_local_time=None), database_url=db_url)
        repo.snooze(created["id"], datetime.now(timezone.utc) + timedelta(hours=2), database_url=db_url)
        assert repo.list_watched(database_url=db_url) == []

    def test_snoozing_keeps_the_row_enabled(self, db_url):
        """The distinction from enabled=False: settings and schedule are kept,
        and the UI has to be able to tell the two states apart."""
        created = repo.create(_values(), database_url=db_url)
        repo.snooze(created["id"], datetime.now(timezone.utc) + timedelta(days=1), database_url=db_url)

        row = repo.get(created["id"], database_url=db_url)
        assert row["enabled"] is True
        assert row["muted_until"] is not None
        assert row["schedule_local_time"] == "08:00"

    def test_it_can_be_cleared(self, db_url):
        created = repo.create(_values(), database_url=db_url)
        repo.snooze(created["id"], datetime.now(timezone.utc) + timedelta(days=1), database_url=db_url)
        repo.snooze(created["id"], None, database_url=db_url)

        assert repo.get(created["id"], database_url=db_url)["muted_until"] is None
        assert len(repo.claim_due(datetime.now(timezone.utc), database_url=db_url)) == 1

    def test_one_snooze_does_not_silence_another_guild(self, db_url):
        mine = repo.create(_values(), database_url=db_url)
        theirs = repo.create(_values(guild_id="2"), database_url=db_url)
        repo.snooze(mine["id"], datetime.now(timezone.utc) + timedelta(days=1), database_url=db_url)

        due = repo.claim_due(datetime.now(timezone.utc), database_url=db_url)
        assert [row["id"] for row in due] == [theirs["id"]]


class TestRunNow:
    def _cog(self):
        from zephyr.cogs.weather_alerts import WeatherAlertsCog

        cog = WeatherAlertsCog.__new__(WeatherAlertsCog)
        cog.bot = MagicMock()
        return cog

    @pytest.mark.asyncio
    async def test_a_manual_send_does_not_mark_the_row_fired(self, monkeypatch):
        """Advancing last_run_at would silently cancel today's real digest."""
        cog = self._cog()
        marked = []
        monkeypatch.setattr("zephyr.cogs.weather_alerts.get_openmeteo_bundle", lambda *a, **k: {"ok": True})
        monkeypatch.setattr(
            "zephyr.cogs.weather_alerts.evaluate",
            lambda *a, **k: {"fingerprint": "abc", "kind": "severe", "title": "t", "lines": []},
        )
        monkeypatch.setattr("zephyr.cogs.weather_alerts.alert_embed", lambda alert: MagicMock())
        monkeypatch.setattr("zephyr.cogs.weather_alerts.mark_fired", lambda *a, **k: marked.append(a))

        channel = MagicMock()
        channel.send = AsyncMock()
        cog.bot.get_channel.return_value = channel

        row = {"id": 1, "lat": 1.0, "lon": 2.0, "units": "metric", "kind": "severe",
               "location": "X", "thresholds": None, "channel_id": "10", "last_fingerprint": None}
        assert await cog._deliver(row, dedupe=True, mark=False) is True
        assert marked == []
        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_it_reports_when_there_is_nothing_to_say(self, monkeypatch):
        cog = self._cog()
        monkeypatch.setattr("zephyr.cogs.weather_alerts.get_openmeteo_bundle", lambda *a, **k: {})
        monkeypatch.setattr("zephyr.cogs.weather_alerts.evaluate", lambda *a, **k: None)

        row = {"id": 1, "lat": 1.0, "lon": 2.0, "units": "metric", "kind": "daily",
               "location": "X", "thresholds": None, "channel_id": "10"}
        assert await cog._deliver(row) is False

    @pytest.mark.asyncio
    async def test_an_unreachable_channel_reports_failure(self, monkeypatch):
        """This is what /weather-preview could not tell you: it showed the
        caller the content privately and never touched the channel."""
        cog = self._cog()
        monkeypatch.setattr("zephyr.cogs.weather_alerts.get_openmeteo_bundle", lambda *a, **k: {})
        monkeypatch.setattr(
            "zephyr.cogs.weather_alerts.evaluate",
            lambda *a, **k: {"fingerprint": "abc", "kind": "daily", "title": "t", "lines": []},
        )
        cog.bot.get_channel.return_value = None

        row = {"id": 1, "lat": 1.0, "lon": 2.0, "units": "metric", "kind": "daily",
               "location": "X", "thresholds": None, "channel_id": "10"}
        assert await cog._deliver(row) is False


class TestTheApi:
    def _headers(self, logged_in):
        return {"X-Zephyr-CSRF": logged_in.csrf}

    def _create(self, client, logged_in):
        return client.post(
            "/api/v1/guilds/1/weather-subs",
            json={"channel_id": "10", "kind": "daily", "location": "Iloilo City",
                  "schedule_local_time": "08:00", "tz": "UTC"},
            headers=self._headers(logged_in),
        ).get_json()

    def test_a_subscription_can_be_snoozed_and_unsnoozed(self, client, logged_in, fake_redis, db_url, monkeypatch):
        monkeypatch.setattr(
            "website.api.weather_subs.geocode_search",
            lambda name, count: [{"name": "Iloilo City", "latitude": 10.7, "longitude": 122.5}],
        )
        created = self._create(client, logged_in)

        until = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        response = client.patch(
            f"/api/v1/guilds/1/weather-subs/{created['id']}",
            json={"muted_until": until},
            headers=self._headers(logged_in),
        )
        assert response.status_code == 200
        assert response.get_json()["muted_until"] is not None
        # Still enabled -- the two states are different things.
        assert response.get_json()["enabled"] is True

        cleared = client.patch(
            f"/api/v1/guilds/1/weather-subs/{created['id']}",
            json={"muted_until": None},
            headers=self._headers(logged_in),
        )
        assert cleared.get_json()["muted_until"] is None

    def test_a_javascript_z_suffix_is_accepted(self, client, logged_in, fake_redis, db_url, monkeypatch):
        """What toISOString emits, and what fromisoformat rejected before 3.11 --
        normalising here means the dashboard does not have to know."""
        monkeypatch.setattr(
            "website.api.weather_subs.geocode_search",
            lambda name, count: [{"name": "Iloilo City", "latitude": 10.7, "longitude": 122.5}],
        )
        created = self._create(client, logged_in)
        response = client.patch(
            f"/api/v1/guilds/1/weather-subs/{created['id']}",
            json={"muted_until": "2026-12-25T00:00:00.000Z"},
            headers=self._headers(logged_in),
        )
        assert response.status_code == 200

    def test_nonsense_is_refused(self, client, logged_in, fake_redis, db_url, monkeypatch):
        monkeypatch.setattr(
            "website.api.weather_subs.geocode_search",
            lambda name, count: [{"name": "Iloilo City", "latitude": 10.7, "longitude": 122.5}],
        )
        created = self._create(client, logged_in)
        response = client.patch(
            f"/api/v1/guilds/1/weather-subs/{created['id']}",
            json={"muted_until": "next tuesday"},
            headers=self._headers(logged_in),
        )
        assert response.status_code == 400
