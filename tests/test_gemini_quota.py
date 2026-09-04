"""The Gemini quota limiter.

Written *before* the Redis rewrite, deliberately. Nothing in tests/ imported any
of the five quota dicts or five quota functions, so this was entirely untested
code -- and a refactor of untested code has nothing to be equal to. Every
assertion here describes the in-memory behaviour as it stood, so the durable
version can be held to the same contract.

No freezegun: `prune_model_usage` already takes `now_utc`, and
`reserve_local_quota` reads the clock through one call, so injecting time is a
monkeypatch rather than a dependency.
"""

from datetime import datetime, timedelta, timezone

import pytest

from zephyr.services import gemini


@pytest.fixture(autouse=True)
def _clean_quota():
    """Module-level state, so it outlives a test. Same pattern as
    tests/test_summarize_cooldown.py's autouse clear."""
    gemini.reset_quota_state()
    yield
    gemini.reset_quota_state()


@pytest.fixture
def model(monkeypatch):
    """A model with small, obvious limits."""
    name = "test-model"
    monkeypatch.setitem(gemini.MODEL_LIMITS, name, {"rpm": 3, "tpm": 1000, "rpd": 5})
    return name


class TestAModelWithNoLimits:
    @pytest.mark.asyncio
    async def test_it_is_always_allowed(self):
        allowed, message = await gemini.reserve_local_quota("unlisted-model", 10)
        assert allowed is True
        assert message is None


class TestRequestsPerMinute:
    @pytest.mark.asyncio
    async def test_it_allows_up_to_the_limit(self, model):
        for _ in range(3):
            allowed, _ = await gemini.reserve_local_quota(model, 1)
            assert allowed is True

    @pytest.mark.asyncio
    async def test_the_next_one_is_refused_with_a_retry_time(self, model):
        for _ in range(3):
            await gemini.reserve_local_quota(model, 1)

        allowed, message = await gemini.reserve_local_quota(model, 1)
        assert allowed is False
        assert "minute" in message.lower() or "rpm" in message.lower()

    @pytest.mark.asyncio
    async def test_the_window_slides(self, model, monkeypatch):
        for _ in range(3):
            await gemini.reserve_local_quota(model, 1)
        assert (await gemini.reserve_local_quota(model, 1))[0] is False

        # Sixty-one seconds later the window is empty again.
        later = datetime.now(timezone.utc) + timedelta(seconds=61)
        monkeypatch.setattr(gemini, "utc_now", lambda: later)
        assert (await gemini.reserve_local_quota(model, 1))[0] is True


class TestTokensPerMinute:
    @pytest.mark.asyncio
    async def test_a_request_that_would_exceed_the_budget_is_refused(self, model):
        await gemini.reserve_local_quota(model, 900)
        allowed, message = await gemini.reserve_local_quota(model, 200)
        assert allowed is False
        assert message

    @pytest.mark.asyncio
    async def test_it_counts_tokens_not_requests(self, model):
        # Two requests is well under rpm=3, so only the token budget can refuse.
        allowed, _ = await gemini.reserve_local_quota(model, 1000)
        assert allowed is True
        assert (await gemini.reserve_local_quota(model, 1))[0] is False


class TestRequestsPerDay:
    @pytest.mark.asyncio
    async def test_the_daily_cap_holds_across_minutes(self, model, monkeypatch):
        now = datetime.now(timezone.utc)
        for index in range(5):
            # Step a couple of minutes each time so rpm never bites.
            monkeypatch.setattr(gemini, "utc_now", lambda n=now + timedelta(minutes=2 * index): n)
            allowed, _ = await gemini.reserve_local_quota(model, 1)
            assert allowed is True, index

        monkeypatch.setattr(gemini, "utc_now", lambda: now + timedelta(minutes=12))
        allowed, message = await gemini.reserve_local_quota(model, 1)
        assert allowed is False
        assert message

    @pytest.mark.asyncio
    async def test_the_day_rolls_over_on_pacific_midnight(self, model, monkeypatch):
        """Pacific, not UTC. Google's free-tier daily counters reset on Pacific
        midnight, and build_local_limit_message computes the rpd retry from
        get_next_pacific_midnight -- so keying this on the UTC date would make
        the reported retry time wrong by up to eight hours."""
        for _ in range(5):
            await gemini.reserve_local_quota(model, 1)
        assert (await gemini.reserve_local_quota(model, 1))[0] is False

        tomorrow = gemini.get_pacific_today() + timedelta(days=1)
        monkeypatch.setattr(gemini, "get_pacific_today", lambda: tomorrow)
        later = datetime.now(timezone.utc) + timedelta(seconds=61)
        monkeypatch.setattr(gemini, "utc_now", lambda: later)

        assert (await gemini.reserve_local_quota(model, 1))[0] is True


class TestCooldowns:
    @pytest.mark.asyncio
    async def test_a_cooldown_refuses_everything_until_it_expires(self, model):
        await gemini.store_model_cooldown(model, 120)
        allowed, message = await gemini.reserve_local_quota(model, 1)
        assert allowed is False
        assert message

    @pytest.mark.asyncio
    async def test_it_lifts_itself(self, model, monkeypatch):
        await gemini.store_model_cooldown(model, 30)
        assert (await gemini.reserve_local_quota(model, 1))[0] is False

        monkeypatch.setattr(gemini, "utc_now", lambda: datetime.now(timezone.utc) + timedelta(seconds=31))
        assert (await gemini.reserve_local_quota(model, 1))[0] is True

    @pytest.mark.asyncio
    async def test_a_zero_retry_after_is_not_a_cooldown(self, model):
        await gemini.store_model_cooldown(model, 0)
        assert (await gemini.reserve_local_quota(model, 1))[0] is True


class TestTotals:
    @pytest.mark.asyncio
    async def test_a_reservation_counts_as_an_outgoing_request(self, model):
        await gemini.reserve_local_quota(model, 1)
        snapshot = await gemini.get_model_usage_snapshot(model)
        assert snapshot["totals"]["session_requests"] == 1
        # Not yet a *successful* one -- the request has not returned.
        assert snapshot["totals"]["successful_requests"] == 0

    @pytest.mark.asyncio
    async def test_a_successful_response_adds_its_token_counts(self, model):
        class Usage:
            prompt_token_count = 30
            candidates_token_count = 12
            total_token_count = 42

        await gemini.record_successful_usage(model, Usage())
        snapshot = await gemini.get_model_usage_snapshot(model)
        assert snapshot["totals"] == {
            "prompt_tokens": 30, "output_tokens": 12, "total_tokens": 42,
            "successful_requests": 1, "session_requests": 0,
        }

    @pytest.mark.asyncio
    async def test_no_usage_metadata_records_nothing(self, model):
        await gemini.record_successful_usage(model, None)
        snapshot = await gemini.get_model_usage_snapshot(model)
        assert snapshot["totals"]["successful_requests"] == 0


class TestTheSnapshot:
    @pytest.mark.asyncio
    async def test_it_reports_every_counter_the_token_command_shows(self, model):
        await gemini.reserve_local_quota(model, 250)
        snapshot = await gemini.get_model_usage_snapshot(model)

        assert snapshot["rpm"] == 1
        assert snapshot["tpm"] == 250
        assert snapshot["rpd"] == 1
        assert snapshot["cooldown_until"] is None
        assert set(snapshot["totals"]) == {
            "prompt_tokens", "output_tokens", "total_tokens",
            "successful_requests", "session_requests",
        }

    @pytest.mark.asyncio
    async def test_it_reports_a_live_cooldown(self, model):
        await gemini.store_model_cooldown(model, 60)
        snapshot = await gemini.get_model_usage_snapshot(model)
        assert snapshot["cooldown_until"] is not None

    @pytest.mark.asyncio
    async def test_an_untouched_model_reads_as_zero_rather_than_missing(self, model):
        snapshot = await gemini.get_model_usage_snapshot(model)
        assert snapshot["rpm"] == 0 and snapshot["tpm"] == 0 and snapshot["rpd"] == 0
