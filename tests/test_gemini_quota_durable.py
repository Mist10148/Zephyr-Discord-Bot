"""The Redis-backed quota store.

The same contract as tests/test_gemini_quota.py, which describes the in-memory
version -- plus the two properties that were the point of the change: the count
survives a restart, and two processes share it.

Uses the hand-written FakeRedis from conftest, which is why the design is
restricted to incr/expire/get/mget/setex/delete: CI runs no service containers,
and conftest calls that "a hard requirement rather than a convenience".
"""

import time
from datetime import timedelta

import pytest

from zephyr.services import gemini, quota

LIMITS = {"rpm": 3, "tpm": 1000, "rpd": 5}
MODEL = "test-model"


@pytest.fixture
def window():
    return int(time.time() // 60), "2026-09-04"


class TestClaiming:
    def test_it_allows_up_to_the_limit(self, fake_redis, window):
        minute, day = window
        for _ in range(3):
            allowed, name, _ = quota.claim(MODEL, minute=minute, day=day, tokens=10, limits=LIMITS)
            assert allowed is True, name

    def test_the_next_one_names_the_ceiling_that_refused(self, fake_redis, window):
        minute, day = window
        for _ in range(3):
            quota.claim(MODEL, minute=minute, day=day, tokens=10, limits=LIMITS)

        allowed, name, retry_after = quota.claim(MODEL, minute=minute, day=day, tokens=10, limits=LIMITS)
        assert allowed is False
        assert name == "rpm"
        assert retry_after > 0

    def test_a_refused_claim_refunds_what_it_took(self, fake_redis, window):
        """The claim-then-refund protocol only works if the refund happens.

        Without it, one refusal would permanently consume a slot in the window:
        the fourth request increments rpm to 4, and every later check compares
        against 4 rather than 3.
        """
        minute, day = window
        for _ in range(3):
            quota.claim(MODEL, minute=minute, day=day, tokens=10, limits=LIMITS)
        quota.claim(MODEL, minute=minute, day=day, tokens=10, limits=LIMITS)  # refused

        snapshot = quota.snapshot(MODEL, minute=minute, day=day)
        assert snapshot["rpm"] == 3
        assert snapshot["tpm"] == 30

    def test_a_token_refusal_refunds_the_request_slot_too(self, fake_redis, window):
        """Otherwise a token refusal would silently eat rpm as well."""
        minute, day = window
        quota.claim(MODEL, minute=minute, day=day, tokens=900, limits=LIMITS)
        allowed, name, _ = quota.claim(MODEL, minute=minute, day=day, tokens=200, limits=LIMITS)
        assert (allowed, name) == (False, "tpm")

        snapshot = quota.snapshot(MODEL, minute=minute, day=day)
        assert snapshot["rpm"] == 1
        assert snapshot["tpm"] == 900

    def test_the_daily_ceiling_is_separate_from_the_minute_one(self, fake_redis, window):
        _, day = window
        base = int(time.time() // 60)
        # A different minute each time, so rpm never bites.
        for index in range(5):
            allowed, name, _ = quota.claim(MODEL, minute=base + index, day=day, tokens=1, limits=LIMITS)
            assert allowed is True, name

        allowed, name, _ = quota.claim(MODEL, minute=base + 9, day=day, tokens=1, limits=LIMITS)
        assert (allowed, name) == (False, "rpd")

    def test_a_new_minute_is_a_new_window(self, fake_redis, window):
        minute, day = window
        for _ in range(3):
            quota.claim(MODEL, minute=minute, day=day, tokens=10, limits=LIMITS)
        assert quota.claim(MODEL, minute=minute, day=day, tokens=10, limits=LIMITS)[0] is False

        assert quota.claim(MODEL, minute=minute + 1, day=day, tokens=10, limits=LIMITS)[0] is True

    def test_a_new_day_is_a_new_daily_bucket(self, fake_redis, window):
        minute, _ = window
        for index in range(5):
            quota.claim(MODEL, minute=minute + index, day="2026-09-04", tokens=1, limits=LIMITS)
        assert quota.claim(MODEL, minute=minute + 9, day="2026-09-04", tokens=1, limits=LIMITS)[0] is False

        assert quota.claim(MODEL, minute=minute + 9, day="2026-09-05", tokens=1, limits=LIMITS)[0] is True

    def test_models_do_not_share_a_budget(self, fake_redis, window):
        minute, day = window
        for _ in range(3):
            quota.claim(MODEL, minute=minute, day=day, tokens=10, limits=LIMITS)
        assert quota.claim("other-model", minute=minute, day=day, tokens=10, limits=LIMITS)[0] is True


class TestCooldowns:
    def test_a_cooldown_refuses_every_claim(self, fake_redis, window):
        minute, day = window
        quota.set_cooldown(MODEL, 120)
        allowed, name, retry_after = quota.claim(MODEL, minute=minute, day=day, tokens=1, limits=LIMITS)
        assert (allowed, name) == (False, "cooldown")
        assert 0 < retry_after <= 120

    def test_it_stores_a_deadline_rather_than_relying_on_the_ttl(self, fake_redis):
        """TTL is not one of the verbs the test double implements, and reading
        the deadline back from the value also means every process computes the
        same remaining time from the same number."""
        quota.set_cooldown(MODEL, 60)
        raw = fake_redis.get(quota.cooldown_key(MODEL))
        assert int(raw) >= int(time.time())

    def test_an_expired_cooldown_stops_refusing(self, fake_redis, window):
        minute, day = window
        quota.set_cooldown(MODEL, 30)
        fake_redis.expire_now(quota.cooldown_key(MODEL))
        assert quota.claim(MODEL, minute=minute, day=day, tokens=1, limits=LIMITS)[0] is True

    def test_a_zero_cooldown_is_not_stored(self, fake_redis):
        quota.set_cooldown(MODEL, 0)
        assert quota.cooldown_remaining(MODEL) == 0


class TestTotalsAndSnapshot:
    def test_totals_accumulate(self, fake_redis, window):
        minute, day = window
        quota.add_totals(MODEL, {"prompt_tokens": 30, "output_tokens": 12, "total_tokens": 42, "successful_requests": 1})
        quota.add_totals(MODEL, {"prompt_tokens": 5, "successful_requests": 1})

        totals = quota.snapshot(MODEL, minute=minute, day=day)["totals"]
        assert totals["prompt_tokens"] == 35
        assert totals["successful_requests"] == 2

    def test_an_untouched_model_reads_as_zero_rather_than_missing(self, fake_redis, window):
        minute, day = window
        snapshot = quota.snapshot(MODEL, minute=minute, day=day)
        assert snapshot["rpm"] == 0 and snapshot["tpm"] == 0 and snapshot["rpd"] == 0
        assert all(value == 0 for value in snapshot["totals"].values())

    def test_clear_forgets_a_model(self, fake_redis, window):
        minute, day = window
        quota.claim(MODEL, minute=minute, day=day, tokens=10, limits=LIMITS)
        quota.set_cooldown(MODEL, 60)

        quota.clear(MODEL, minute=minute, day=day)
        snapshot = quota.snapshot(MODEL, minute=minute, day=day)
        assert snapshot["rpm"] == 0 and snapshot["rpd"] == 0
        assert snapshot["cooldown_seconds"] == 0


class TestTheWholePointOfThis:
    def test_the_daily_count_survives_a_restart(self, fake_redis, window):
        """The defect this closes. A restart handed out a fresh daily allowance
        the key does not have, so the next burst hit Google's 429 rather than
        the local limiter that exists to prevent exactly that."""
        minute, day = window
        for index in range(5):
            quota.claim(MODEL, minute=minute + index, day=day, tokens=1, limits=LIMITS)

        # A "restart" is a fresh process reading the same Redis: nothing in this
        # module holds state between calls.
        gemini.reset_quota_state()

        allowed, name, _ = quota.claim(MODEL, minute=minute + 9, day=day, tokens=1, limits=LIMITS)
        assert (allowed, name) == (False, "rpd")

    def test_two_processes_see_one_budget(self, fake_redis, window):
        """Both the bot and the web tier reach these counters, and an
        asyncio.Lock protected a read-check-write inside one event loop and
        nothing at all across two processes."""
        minute, day = window
        # Two callers, one Redis. Three claims total is the ceiling.
        assert quota.claim(MODEL, minute=minute, day=day, tokens=1, limits=LIMITS)[0] is True
        assert quota.claim(MODEL, minute=minute, day=day, tokens=1, limits=LIMITS)[0] is True
        assert quota.claim(MODEL, minute=minute, day=day, tokens=1, limits=LIMITS)[0] is True
        assert quota.claim(MODEL, minute=minute, day=day, tokens=1, limits=LIMITS)[0] is False


class TestTheGeminiIntegration:
    @pytest.fixture(autouse=True)
    def _clean(self):
        gemini.reset_quota_state()
        yield
        gemini.reset_quota_state()

    @pytest.fixture
    def durable(self, monkeypatch, fake_redis):
        """Make _durable_quota_url() answer, which inert_env otherwise blanks."""
        monkeypatch.setattr("zephyr.config.REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setitem(gemini.MODEL_LIMITS, MODEL, LIMITS)
        return fake_redis

    @pytest.mark.asyncio
    async def test_reserve_uses_redis_when_it_is_configured(self, durable):
        allowed, message = await gemini.reserve_local_quota(MODEL, 10)
        assert (allowed, message) == (True, None)
        # In Redis, not in the module dicts.
        assert len(gemini.model_request_windows[MODEL]) == 0
        minute, day = gemini._quota_window()
        assert quota.snapshot(MODEL, minute=minute, day=day)["rpm"] == 1

    @pytest.mark.asyncio
    async def test_the_snapshot_still_reports_an_absolute_cooldown(self, durable):
        """The in-memory shape reports a datetime and both /token and the
        dashboard render it, so the durable store converts rather than changing
        the contract for one of the two."""
        await gemini.store_model_cooldown(MODEL, 90)
        snapshot = await gemini.get_model_usage_snapshot(MODEL)
        assert snapshot["cooldown_until"] is not None
        assert snapshot["cooldown_until"] > gemini.utc_now()
        assert snapshot["cooldown_until"] <= gemini.utc_now() + timedelta(seconds=91)

    @pytest.mark.asyncio
    async def test_the_daily_key_is_the_pacific_date(self, durable):
        """Google's free-tier daily counters reset on Pacific midnight, and
        build_local_limit_message computes the rpd retry from
        get_next_pacific_midnight -- so a UTC key would make the reported retry
        wrong by up to eight hours."""
        _, day = gemini._quota_window()
        assert day == gemini.get_pacific_today().isoformat()

    @pytest.mark.asyncio
    async def test_a_redis_outage_falls_back_rather_than_failing_the_request(self, durable, caplog):
        """Less accurate, not less safe: the remote limits still apply and a 429
        is still handled."""
        durable.raise_on = RuntimeError("connection refused")
        with caplog.at_level("WARNING", logger="zephyr.services.gemini"):
            allowed, message = await gemini.reserve_local_quota(MODEL, 10)

        assert (allowed, message) == (True, None)
        assert "falling back to in-memory" in caplog.text
        # It really did fall back: the in-memory window has the reservation.
        assert len(gemini.model_request_windows[MODEL]) == 1

    @pytest.mark.asyncio
    async def test_with_no_redis_it_stays_in_memory(self, monkeypatch):
        monkeypatch.setattr("zephyr.config.REDIS_URL", None)
        monkeypatch.setitem(gemini.MODEL_LIMITS, MODEL, LIMITS)
        await gemini.reserve_local_quota(MODEL, 10)
        assert len(gemini.model_request_windows[MODEL]) == 1
