"""Per-IP limits on the public endpoints.

`rate_limit()` keys on `session.sid` and returns True when there is no session,
so it **fails open for exactly the traffic a public limiter exists to bound** --
which is why it could not simply be applied to `/weather` and friends. Its only
caller before this was `/player`.

No service containers, per conftest's hard requirement: the FakeRedis double
counts for real, and `raise_on` stands in for an outage.
"""

import pytest


def _ip(address):
    """A distinct client address for the test client."""
    return {"REMOTE_ADDR": address}


class TestThePublicLimit:
    def test_the_nth_call_is_refused(self, public_app, fake_redis):
        client = public_app.test_client()
        # /status is the generous one: 120 per minute.
        for _ in range(120):
            assert client.get("/api/v1/status", environ_base=_ip("1.1.1.1")).status_code == 200

        refused = client.get("/api/v1/status", environ_base=_ip("1.1.1.1"))
        assert refused.status_code == 429
        assert refused.get_json()["error"]["code"] == "rate_limited"

    def test_a_refusal_says_when_to_come_back(self, public_app, fake_redis):
        client = public_app.test_client()
        for _ in range(121):
            response = client.get("/api/v1/status", environ_base=_ip("1.1.1.2"))
        # lib/api.ts already throws ApiError(429) and lib/query.ts treats
        # sub-500 as non-retryable, so this surfaces as an error toast with no
        # frontend change -- but a Retry-After is what makes it actionable.
        assert response.headers["Retry-After"] == "60"

    def test_two_addresses_get_two_budgets(self, public_app, fake_redis):
        client = public_app.test_client()
        for _ in range(30):
            client.get("/api/v1/geocode?q=manila", environ_base=_ip("2.2.2.1"))
        assert client.get("/api/v1/geocode?q=manila", environ_base=_ip("2.2.2.1")).status_code == 429
        # A shared budget would make one noisy visitor deny the whole internet.
        assert client.get("/api/v1/geocode?q=manila", environ_base=_ip("2.2.2.2")).status_code != 429

    def test_two_endpoints_have_two_budgets(self, public_app, fake_redis):
        client = public_app.test_client()
        for _ in range(30):
            client.get("/api/v1/geocode?q=x", environ_base=_ip("3.3.3.3"))
        assert client.get("/api/v1/geocode?q=x", environ_base=_ip("3.3.3.3")).status_code == 429
        # Exhausting the geocoder must not close the command reference.
        assert client.get("/api/v1/commands", environ_base=_ip("3.3.3.3")).status_code == 200


class TestPrivacyAndTrust:
    def test_redis_never_holds_a_raw_address(self, public_app, fake_redis):
        """A claim the privacy policy can then make truthfully."""
        client = public_app.test_client()
        client.get("/api/v1/status", environ_base=_ip("198.51.100.7"))

        keys = "\n".join(fake_redis.store)
        assert "198.51.100.7" not in keys
        assert ":ip:" in keys

    def test_a_forged_forwarded_header_is_ignored_without_a_proxy(self, public_app, fake_redis):
        """ProxyFix is installed only when TRUST_PROXY is on. Reading
        X-Forwarded-For directly would let an unproxied client mint itself an
        unlimited budget, one request per forged address."""
        client = public_app.test_client()
        for index in range(121):
            response = client.get(
                "/api/v1/status",
                environ_base=_ip("4.4.4.4"),
                headers={"X-Forwarded-For": f"9.9.9.{index}"},
            )
        assert response.status_code == 429


class TestFailingOpen:
    def test_a_redis_outage_does_not_take_the_page_down(self, public_app, fake_redis, caplog):
        """The limiter exists to protect an upstream quota, not to become a new
        single point of failure for the weather page."""
        fake_redis.raise_on = RuntimeError("connection refused")
        client = public_app.test_client()
        with caplog.at_level("WARNING", logger="website.api.guard"):
            assert client.get("/api/v1/status", environ_base=_ip("5.5.5.5")).status_code == 200
        assert "failing open" in caplog.text

    def test_no_redis_configured_does_not_refuse(self, fake_redis):
        from website import create_app

        app = create_app({"TESTING": True, "AUTH_ENABLED": False, "REDIS_URL": None})
        assert app.test_client().get("/api/v1/status").status_code == 200


class TestTheWindowRolls:
    def test_a_new_window_is_a_new_budget(self, public_app, fake_redis, monkeypatch):
        client = public_app.test_client()
        for _ in range(31):
            response = client.get("/api/v1/geocode?q=y", environ_base=_ip("6.6.6.6"))
        assert response.status_code == 429

        # The key embeds `time() // window`, so the next window is a new key.
        import website.api.guard as guard

        real = guard.time.time
        monkeypatch.setattr(guard.time, "time", lambda: real() + 61)
        assert client.get("/api/v1/geocode?q=y", environ_base=_ip("6.6.6.6")).status_code != 429


class TestTheSessionLimiterIsUnchanged:
    def test_it_still_returns_true_for_an_anonymous_caller(self, app):
        """Load-bearing: this is *why* a separate per-IP limiter exists rather
        than bolting an anonymous branch onto this one, which would make one
        function mean two things."""
        from website.api.guard import rate_limit

        with app.test_request_context("/api/v1/status"):
            assert rate_limit("anything", limit=1, window=60) is True
            assert rate_limit("anything", limit=1, window=60) is True


class TestLoginIsTheTightest:
    def test_it_answers_with_a_redirect_not_json(self, app, fake_redis):
        """Reached by a browser navigation, so a JSON envelope would put a raw
        error object on screen. Every call also mints a Redis state key, which
        makes this the cheapest way to fill the session store."""
        client = app.test_client()
        for _ in range(11):
            response = client.get("/api/v1/auth/login", environ_base=_ip("7.7.7.7"))

        assert response.status_code in (302, 303)
        assert "rate_limited" in response.headers["Location"]

    def test_a_normal_sign_in_is_not_affected(self, app, fake_redis):
        client = app.test_client()
        response = client.get("/api/v1/auth/login", environ_base=_ip("8.8.8.8"))
        assert response.status_code in (302, 303)
        assert "rate_limited" not in response.headers["Location"]
