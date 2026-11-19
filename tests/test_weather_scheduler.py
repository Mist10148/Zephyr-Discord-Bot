"""Tests for the weather-alerts scheduler's delivery orchestration.

The *deciding* (what an alert says) is covered by test_weather_alerts.py against
the pure functions in zephyr.utils.weather_alerts. This covers the other half the
plan names for Phase 7: WeatherAlertsCog._deliver, which turns a decision into a
posted message -- and, crucially, decides when NOT to post and when NOT to record
a run. Those two "nots" are the whole reason the loops are shaped the way they are.

Async methods are driven with asyncio.run so no pytest-asyncio dependency is needed;
_deliver uses asyncio.to_thread internally, which asyncio.run handles.
"""

import asyncio

import pytest

from zephyr.cogs import weather_alerts as cog_module
from zephyr.cogs.weather_alerts import WeatherAlertsCog

ALERT = {"kind": "severe", "title": "⚠️ Severe", "summary": "Windy", "fields": [], "fingerprint": "abc123"}


class FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id


class FakeChannel:
    def __init__(self, guild_id=42):
        self.sent = []
        self.guild = FakeGuild(guild_id)

    async def send(self, *, embed=None):
        self.sent.append(embed)


class FakeBot:
    def __init__(self, channel):
        self._channel = channel

    def get_channel(self, _id):
        return self._channel


@pytest.fixture
def fired(monkeypatch):
    """Capture mark_fired calls; stub the provider so no network is touched."""
    calls = []
    monkeypatch.setattr(cog_module, "get_openmeteo_bundle", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cog_module, "mark_fired", lambda sub_id, *, fingerprint: calls.append((sub_id, fingerprint)))
    return calls


def _sub(**over):
    base = {"id": 7, "lat": 10.0, "lon": 122.0, "units": "metric", "location": "X",
            "kind": "severe", "channel_id": "555", "guild_id": "42", "thresholds": None, "last_fingerprint": None}
    base.update(over)
    return base


def _deliver(cog, sub, dedupe):
    asyncio.run(cog._deliver(sub, dedupe=dedupe))


class TestWatchDelivery:
    def test_a_new_alert_is_posted_and_recorded(self, monkeypatch, fired):
        monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: ALERT)
        channel = FakeChannel()
        _deliver(WeatherAlertsCog(FakeBot(channel)), _sub(), dedupe=True)
        assert len(channel.sent) == 1
        assert fired == [(7, "abc123")]

    def test_a_quiet_tick_posts_nothing_and_records_nothing(self, monkeypatch, fired):
        """evaluate returns None -> the row must be left untouched, or the next
        genuine warning looks like a duplicate."""
        monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: None)
        channel = FakeChannel()
        _deliver(WeatherAlertsCog(FakeBot(channel)), _sub(), dedupe=True)
        assert channel.sent == []
        assert fired == []

    def test_a_repeat_of_the_last_fingerprint_is_suppressed(self, monkeypatch, fired):
        monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: ALERT)
        channel = FakeChannel()
        _deliver(WeatherAlertsCog(FakeBot(channel)), _sub(last_fingerprint="abc123"), dedupe=True)
        assert channel.sent == []
        assert fired == []

    def test_an_unreachable_channel_is_skipped_not_recorded(self, monkeypatch, fired):
        monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: ALERT)
        _deliver(WeatherAlertsCog(FakeBot(None)), _sub(), dedupe=True)
        assert fired == []


class TestDigestDelivery:
    def test_a_digest_posts_without_recording_a_fingerprint(self, monkeypatch, fired):
        """Digests are claimed in the query, not deduped here, so _deliver must
        not call mark_fired for them -- doing so is harmless but signals the wrong
        model, and the test locks the intended path."""
        monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: {**ALERT, "kind": "daily"})
        channel = FakeChannel()
        _deliver(WeatherAlertsCog(FakeBot(channel)), _sub(kind="daily"), dedupe=False)
        assert len(channel.sent) == 1
        assert fired == []

    def test_a_provider_failure_is_contained(self, monkeypatch, fired):
        from zephyr.utils.weather_utils import WeatherProviderError

        def boom(*a, **k):
            raise WeatherProviderError("provider down")

        monkeypatch.setattr(cog_module, "get_openmeteo_bundle", boom)
        channel = FakeChannel()
        # Must not raise: one bad subscription cannot stop the batch.
        _deliver(WeatherAlertsCog(FakeBot(channel)), _sub(), dedupe=True)
        assert channel.sent == [] and fired == []

    def test_a_channel_in_another_guild_is_never_posted_to(self, monkeypatch, fired):
        """channel_id is only checked for shape; get_channel spans every guild."""
        monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: ALERT)
        channel = FakeChannel(guild_id=99)
        _deliver(WeatherAlertsCog(FakeBot(channel)), _sub(), dedupe=True)
        assert channel.sent == [] and fired == []


class TestLoopSurvival:
    """An exception escaping a tasks.loop body ends that loop for every guild."""

    def test_an_evaluator_crash_is_contained(self, monkeypatch, fired):
        def boom(*a, **k):
            raise TypeError("'>=' not supported between 'float' and 'str'")

        monkeypatch.setattr(cog_module, "evaluate", boom)
        asyncio.run(WeatherAlertsCog(FakeBot(FakeChannel()))._deliver_safely(_sub(), dedupe=True))

    def test_a_mark_fired_failure_is_contained(self, monkeypatch):
        monkeypatch.setattr(cog_module, "get_openmeteo_bundle", lambda *a, **k: {"ok": True})
        monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: ALERT)

        def boom(*a, **k):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(cog_module, "mark_fired", boom)
        channel = FakeChannel()
        asyncio.run(WeatherAlertsCog(FakeBot(channel))._deliver_safely(_sub(), dedupe=True))
        assert len(channel.sent) == 1

    def test_one_bad_row_does_not_stop_the_batch(self, monkeypatch, fired):
        monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: ALERT)
        channel = FakeChannel()
        monkeypatch.setattr(cog_module, "list_watched", lambda: [_sub(id=1, channel_id="²"), _sub(id=2)])

        asyncio.run(WeatherAlertsCog._watch_loop.coro(WeatherAlertsCog(FakeBot(channel))))

        assert len(channel.sent) == 1
        assert fired == [(2, "abc123")]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_a_digest_that_failed_to_load_is_released_for_retry(monkeypatch):
    released = []

    def provider_down(*a, **k):
        raise cog_module.WeatherProviderError("down")

    monkeypatch.setattr(cog_module, "claim_due", lambda now: [_sub(kind="daily", last_run_at=None)])
    monkeypatch.setattr(cog_module, "get_openmeteo_bundle", provider_down)
    monkeypatch.setattr(cog_module, "release_claim", lambda sub_id, previous: released.append((sub_id, previous)))
    cog = WeatherAlertsCog(FakeBot(FakeChannel()))

    asyncio.run(cog._digest_loop.coro(cog))

    assert released == [(7, None)]


def test_a_missing_channel_is_not_retried_every_minute(monkeypatch, fired):
    released = []
    monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: dict(ALERT, kind="daily"))
    monkeypatch.setattr(cog_module, "claim_due", lambda now: [_sub(kind="daily")])
    monkeypatch.setattr(cog_module, "release_claim", lambda *a: released.append(a))
    cog = WeatherAlertsCog(FakeBot(None))

    asyncio.run(cog._digest_loop.coro(cog))

    assert released == []


def test_a_refused_permission_gets_a_reply(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from discord import app_commands

    interaction = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    cog = WeatherAlertsCog(FakeBot(None))

    asyncio.run(cog.cog_app_command_error(interaction, app_commands.MissingPermissions(["manage_guild"])))

    assert "Manage Server" in interaction.response.send_message.await_args.args[0]


def test_a_quiet_tick_forgets_the_last_alert(monkeypatch, fired):
    """The severe fingerprint has no date: without this, the next day's identical
    afternoon storm matched the previous one and was never announced."""
    cleared = []
    monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: None)
    monkeypatch.setattr(cog_module, "clear_fingerprint", lambda sub_id: cleared.append(sub_id))
    channel = FakeChannel()

    _deliver(WeatherAlertsCog(FakeBot(channel)), _sub(last_fingerprint="abc123"), dedupe=True)
    _deliver(WeatherAlertsCog(FakeBot(channel)), _sub(last_fingerprint=None), dedupe=True)

    assert cleared == [7]
    assert channel.sent == []


def test_a_client_error_is_not_retried_but_a_server_error_is(monkeypatch, fired):
    import discord

    monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: dict(ALERT, kind="daily"))

    def failing(status):
        class Channel(FakeChannel):
            async def send(self, *, embed=None):
                response = type("R", (), {"status": status, "reason": "x"})()
                raise discord.HTTPException(response, "nope")
        return Channel()

    bad_request = WeatherAlertsCog(FakeBot(failing(400)))
    assert asyncio.run(bad_request._deliver(_sub(kind="daily"))) is True
    outage = WeatherAlertsCog(FakeBot(failing(503)))
    assert asyncio.run(outage._deliver(_sub(kind="daily"))) is False


def test_a_cold_day_adds_no_classes_line_to_the_digest():
    alert = {"kind": "daily", "title": "t", "summary": "s", "fields": [],
             "class_suspension": {"level": "possible", "reason": "Cold Weather"}}
    assert all("Classes" not in field.name for field in cog_module.alert_embed(alert).fields)
    alert["class_suspension"] = {"level": "likely", "reason": "Extreme Heat"}
    assert any("Classes" in field.name for field in cog_module.alert_embed(alert).fields)


def test_a_quiet_tick_inside_the_severe_window_keeps_the_watch(monkeypatch, fired):
    """Rain wobbling 80 -> 75 -> 85 % posted the same storm twice."""
    from datetime import datetime, timedelta, timezone

    cleared = []
    monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: None)
    monkeypatch.setattr(cog_module, "clear_fingerprint", lambda sub_id: cleared.append(sub_id))
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    old = (datetime.now(timezone.utc) - timedelta(hours=13)).replace(tzinfo=None)  # SQLite-naive

    _deliver(WeatherAlertsCog(FakeBot(FakeChannel())), _sub(last_fingerprint="abc", last_run_at=recent), dedupe=True)
    assert cleared == []
    _deliver(WeatherAlertsCog(FakeBot(FakeChannel())), _sub(last_fingerprint="abc", last_run_at=old), dedupe=True)
    assert cleared == [7]


def test_a_quiet_tick_never_clears_a_class_watch(monkeypatch, fired):
    """Its fingerprint carries the date; clearing made a wobbling peak repost the same day."""
    cleared = []
    monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: None)
    monkeypatch.setattr(cog_module, "clear_fingerprint", lambda sub_id: cleared.append(sub_id))

    _deliver(WeatherAlertsCog(FakeBot(FakeChannel())),
             _sub(kind="class_suspension", last_fingerprint="abc", last_run_at=None), dedupe=True)
    assert cleared == []


def test_weather_preview_says_when_a_watch_would_stay_quiet(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    monkeypatch.setattr(cog_module, "get", lambda sub_id: _sub(last_fingerprint="abc123"))
    monkeypatch.setattr(cog_module, "get_openmeteo_bundle", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cog_module, "evaluate", lambda *a, **k: ALERT)
    followup = AsyncMock()
    interaction = SimpleNamespace(guild=SimpleNamespace(id=42),
                                  response=SimpleNamespace(defer=AsyncMock()),
                                  followup=SimpleNamespace(send=followup))

    asyncio.run(WeatherAlertsCog.preview.callback(WeatherAlertsCog(FakeBot(None)), interaction, 7))

    assert "Already announced" in followup.await_args.args[0]


def test_subscribe_options_are_bounded():
    params = {p.name: p for p in WeatherAlertsCog.subscribe.parameters}
    assert params["location"].max_value == 100
    assert params["tz"].max_value == 64


def test_subscribing_from_a_thread_is_refused():
    """An archived thread leaves the cache, and every post was then dropped."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    import discord

    thread = MagicMock(spec=discord.Thread)
    respond = AsyncMock()
    interaction = SimpleNamespace(guild=SimpleNamespace(id=42, me=object()), channel=thread,
                                  response=SimpleNamespace(send_message=respond, defer=AsyncMock()))
    kind = SimpleNamespace(value="daily")

    asyncio.run(WeatherAlertsCog.subscribe.callback(WeatherAlertsCog(FakeBot(None)), interaction, kind, "Iloilo"))

    assert "not a thread" in respond.await_args.args[0]
    interaction.response.defer.assert_not_awaited()
