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


class FakeChannel:
    def __init__(self):
        self.sent = []

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
            "kind": "severe", "channel_id": "555", "thresholds": None, "last_fingerprint": None}
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
