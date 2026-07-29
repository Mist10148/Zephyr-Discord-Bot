"""Tests for the Discord OAuth2 flow.

Every Discord call is patched and Redis is the in-memory double from conftest, so
no network calls happen.
"""

from urllib.parse import parse_qs, urlsplit

import pytest

from website import discord_api
from website.session import SESSION_PREFIX, STATE_PREFIX, SessionStoreError
from website.api.guard import CSRF_HEADER

STATE_COOKIE = "zephyr_oauth_state"


def _make_user(user_id="900000000000000001"):
    return {"id": user_id, "username": "tester", "global_name": "Tester", "avatar": "avhash"}


def _make_token(scope="identify guilds"):
    return {"access_token": "at", "token_type": "Bearer", "scope": scope, "expires_in": 604800}


def _make_guild(guild_id="1", owner=True, permissions="0"):
    return {"id": guild_id, "name": f"Guild {guild_id}", "icon": None, "owner": owner, "permissions": permissions}


def _query(response):
    return parse_qs(urlsplit(response.headers["Location"]).query)


def _error_code(response):
    return _query(response).get("error", [None])[0]


def _begin_login(client, next_path=None):
    """Run /auth/login and return the state it minted."""
    path = "/api/v1/auth/login"
    if next_path is not None:
        path += f"?next={next_path}"
    response = client.get(path)
    return _query(response)["state"][0], response


def _patch_discord(monkeypatch, *, token=None, user=None, guilds=None):
    monkeypatch.setattr(discord_api, "exchange_code", lambda *a, **k: token or _make_token())
    monkeypatch.setattr(discord_api, "get_current_user", lambda *a, **k: user or _make_user())
    monkeypatch.setattr(discord_api, "get_current_user_guilds", lambda *a, **k: guilds or [_make_guild()])


class TestSafeNext:
    """Open-redirect guard on the post-login return path."""

    @pytest.mark.parametrize(
        "value",
        ["https://evil.com", "//evil.com", "/\\evil.com", "evil.com", "", None, "/" + "x" * 300],
    )
    def test_rejected(self, app, value):
        from website.api.auth import safe_next

        with app.test_request_context():
            assert safe_next(value) is None

    @pytest.mark.parametrize("value", ["/", "/g", "/g/123456789", "/g/1?tab=music"])
    def test_accepted(self, app, value):
        from website.api.auth import safe_next

        with app.test_request_context():
            assert safe_next(value) == value


class TestAuthLogin:
    def test_not_configured_redirects_to_login(self, public_app):
        response = public_app.test_client().get("/api/v1/auth/login")
        assert response.status_code == 302
        assert _error_code(response) == "not_configured"

    def test_redirects_to_discord_with_the_right_query(self, client, app):
        response = client.get("/api/v1/auth/login")
        assert response.status_code == 302
        location = urlsplit(response.headers["Location"])
        assert location.netloc == "discord.com"
        query = parse_qs(location.query)
        assert query["client_id"] == [app.config["DISCORD_CLIENT_ID"]]
        assert query["scope"] == ["identify guilds"]
        assert query["redirect_uri"] == [app.config["DISCORD_REDIRECT_URI"]]
        assert query["response_type"] == ["code"]
        assert query["prompt"] == ["none"]
        assert len(query["state"][0]) >= 32

    def test_stores_the_state_in_redis_with_a_ttl(self, client, app, fake_redis):
        state, _ = _begin_login(client)
        remaining = fake_redis.ttl_of(STATE_PREFIX + state)
        assert 0 < remaining <= app.config["OAUTH_STATE_TTL"]

    def test_also_binds_the_state_to_the_browser(self, client):
        """Redis alone would leave login-CSRF open."""
        state, response = _begin_login(client)
        cookie = next(h for h in response.headers.getlist("Set-Cookie") if h.startswith(STATE_COOKIE))
        assert state in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie

    def test_is_not_cacheable(self, client):
        assert client.get("/api/v1/auth/login").headers["Cache-Control"] == "no-store"

    def test_a_redis_outage_fails_closed(self, client, fake_redis):
        fake_redis.raise_on = ConnectionError("redis is down")
        assert _error_code(client.get("/api/v1/auth/login")) == "session_unavailable"


class TestAuthCallback:
    def _callback(self, client, state, code="the-code", **params):
        query = {"code": code, "state": state, **params}
        client.set_cookie(STATE_COOKIE, state, domain="localhost")
        return client.get("/api/v1/auth/callback", query_string=query)

    def test_happy_path(self, client, app, fake_redis, monkeypatch):
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client)
        response = self._callback(client, state)
        assert response.status_code == 302
        assert urlsplit(response.headers["Location"]).path == app.config["SPA_DEFAULT_PATH"]

    def test_sets_both_cookies_with_the_right_attributes(self, client, app, monkeypatch):
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client)
        cookies = self._callback(client, state).headers.getlist("Set-Cookie")
        session_cookie = next(c for c in cookies if c.startswith(app.config["AUTH_COOKIE_NAME"]))
        csrf_cookie = next(c for c in cookies if c.startswith(app.config["CSRF_COOKIE_NAME"]))
        assert "HttpOnly" in session_cookie
        # Lax is mandatory: Strict is dropped on the cross-site redirect back from
        # discord.com, so the session would vanish exactly once, mysteriously.
        assert "SameSite=Lax" in session_cookie
        assert "Path=/" in session_cookie
        # The CSRF cookie is readable on purpose -- it only transports the token.
        assert "HttpOnly" not in csrf_cookie

    def test_creates_a_session_holding_the_manageable_guilds(self, client, app, fake_redis, monkeypatch):
        _patch_discord(
            monkeypatch,
            guilds=[
                _make_guild("1", owner=True),
                _make_guild("2", owner=False, permissions="0"),
                _make_guild("3", owner=False, permissions=str(1 << 5)),
            ],
        )
        state, _ = _begin_login(client)
        self._callback(client, state)
        keys = [k for k in fake_redis.store if k.startswith(SESSION_PREFIX)]
        assert len(keys) == 1
        import json

        stored = json.loads(fake_redis.get(keys[0]))
        assert {g["id"] for g in stored["guilds"]} == {"1", "3"}
        assert stored["user_id"] == "900000000000000001"

    def test_records_the_web_user(self, client, app, monkeypatch):
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client)
        self._callback(client, state)
        from sqlalchemy import select

        from zephyr.db.models import WebUser
        from zephyr.db.session import get_engine

        engine = get_engine(app.config["DATABASE_URL"])
        with engine.connect() as connection:
            rows = connection.execute(select(WebUser.discord_id, WebUser.refresh_token_enc)).all()
        assert rows == [("900000000000000001", None)]

    def test_consumes_the_state(self, client, fake_redis, monkeypatch):
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client)
        self._callback(client, state)
        assert fake_redis.get(STATE_PREFIX + state) is None

    def test_replaying_the_callback_fails(self, client, monkeypatch):
        """GETDEL makes the state single-use."""
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client)
        assert _error_code(self._callback(client, state)) is None
        assert _error_code(self._callback(client, state)) == "state_expired"

    def test_honours_a_safe_next(self, client, monkeypatch):
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client, "%2Fg%2F123456789")
        response = self._callback(client, state)
        assert urlsplit(response.headers["Location"]).path == "/g/123456789"

    def test_drops_an_unsafe_next(self, client, app, monkeypatch):
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client, "https%3A%2F%2Fevil.com")
        response = self._callback(client, state)
        location = urlsplit(response.headers["Location"])
        assert location.netloc == ""
        assert location.path == app.config["SPA_DEFAULT_PATH"]

    def test_rotates_the_session_id(self, client, app, fake_redis, monkeypatch, logged_in):
        """Session fixation: a fresh callback must not reuse the presented id."""
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client)
        self._callback(client, state)
        keys = [k for k in fake_redis.store if k.startswith(SESSION_PREFIX)]
        assert keys == [k for k in keys if k != SESSION_PREFIX + logged_in.sid]
        assert fake_redis.get(SESSION_PREFIX + logged_in.sid) is None

    def test_an_audit_row_failure_does_not_fail_the_login(self, client, app, monkeypatch):
        _patch_discord(monkeypatch)

        def explode(*_args, **_kwargs):
            raise RuntimeError("postgres is down")

        monkeypatch.setattr("website.api.auth.upsert_web_user", explode)
        state, _ = _begin_login(client)
        response = self._callback(client, state)
        assert urlsplit(response.headers["Location"]).path == app.config["SPA_DEFAULT_PATH"]

    def test_not_configured(self, public_app):
        response = public_app.test_client().get("/api/v1/auth/callback?code=c&state=s")
        assert _error_code(response) == "not_configured"

    def test_user_cancelled(self, client):
        response = client.get("/api/v1/auth/callback?error=access_denied&state=s")
        assert _error_code(response) == "access_denied"

    def test_other_upstream_errors_are_not_echoed_verbatim(self, client):
        response = client.get("/api/v1/auth/callback?error=server_error&state=s")
        assert _error_code(response) == "oauth_error"

    @pytest.mark.parametrize("query", ["", "code=c", "state=s"])
    def test_missing_parameters(self, client, query):
        response = client.get(f"/api/v1/auth/callback?{query}")
        assert _error_code(response) == "invalid_request"

    def test_state_mismatch_clears_the_state_cookie(self, client, monkeypatch):
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client)
        client.set_cookie(STATE_COOKIE, "tampered", domain="localhost")
        response = client.get("/api/v1/auth/callback", query_string={"code": "c", "state": state})
        assert _error_code(response) == "state_mismatch"
        cleared = next(c for c in response.headers.getlist("Set-Cookie") if c.startswith(STATE_COOKIE))
        assert "Max-Age=0" in cleared or "Expires=Thu, 01 Jan 1970" in cleared

    def test_a_missing_state_cookie_is_a_mismatch(self, client, monkeypatch):
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client)
        # The test client keeps cookies from /auth/login, so drop it explicitly to
        # model a browser that never received or has since lost it.
        client.delete_cookie(STATE_COOKIE, domain="localhost")
        response = client.get("/api/v1/auth/callback", query_string={"code": "c", "state": state})
        assert _error_code(response) == "state_mismatch"

    def test_an_unknown_state_is_expired(self, client):
        response = self._callback(client, "never-stored")
        assert _error_code(response) == "state_expired"

    def test_token_exchange_failure(self, client, monkeypatch):
        state, _ = _begin_login(client)

        def explode(*_args, **_kwargs):
            raise discord_api.DiscordUpstreamError("invalid_grant")

        monkeypatch.setattr(discord_api, "exchange_code", explode)
        assert _error_code(self._callback(client, state)) == "token_exchange_failed"

    def test_a_token_response_without_a_token(self, client, monkeypatch):
        _patch_discord(monkeypatch, token={"scope": "identify guilds"})
        state, _ = _begin_login(client)
        assert _error_code(self._callback(client, state)) == "token_exchange_failed"

    def test_insufficient_scope(self, client, monkeypatch):
        """A user can untick scopes on the consent screen."""
        _patch_discord(monkeypatch, token=_make_token(scope="identify"))
        state, _ = _begin_login(client)
        assert _error_code(self._callback(client, state)) == "insufficient_scope"

    def test_discord_timeout_on_the_user_lookup(self, client, monkeypatch):
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client)

        def explode(*_args, **_kwargs):
            raise discord_api.DiscordTimeoutError("timed out")

        monkeypatch.setattr(discord_api, "get_current_user", explode)
        assert _error_code(self._callback(client, state)) == "discord_unavailable"

    def test_discord_rate_limited(self, client, monkeypatch):
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client)

        def explode(*_args, **_kwargs):
            raise discord_api.DiscordRateLimited(30.0)

        monkeypatch.setattr(discord_api, "get_current_user_guilds", explode)
        assert _error_code(self._callback(client, state)) == "discord_rate_limited"

    def test_a_session_write_failure_fails_closed(self, client, fake_redis, monkeypatch):
        _patch_discord(monkeypatch)
        state, _ = _begin_login(client)
        monkeypatch.setattr(
            "website.api.auth.create_session",
            lambda *a, **k: (_ for _ in ()).throw(SessionStoreError("redis is down")),
        )
        assert _error_code(self._callback(client, state)) == "session_unavailable"


class TestLogout:
    def test_with_no_session_is_still_a_success(self, client):
        """403 here would leak whether a session existed."""
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 204

    def test_clears_both_cookies(self, client, app, logged_in):
        response = client.post("/api/v1/auth/logout", headers={CSRF_HEADER: logged_in.csrf})
        assert response.status_code == 204
        cleared = " ".join(response.headers.getlist("Set-Cookie"))
        assert app.config["AUTH_COOKIE_NAME"] in cleared
        assert app.config["CSRF_COOKIE_NAME"] in cleared

    def test_deletes_the_session(self, client, fake_redis, logged_in):
        client.post("/api/v1/auth/logout", headers={CSRF_HEADER: logged_in.csrf})
        assert fake_redis.get(SESSION_PREFIX + logged_in.sid) is None

    def test_without_a_csrf_token(self, client, logged_in):
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "csrf_failed"

    def test_with_the_wrong_csrf_token(self, client, logged_in):
        response = client.post("/api/v1/auth/logout", headers={CSRF_HEADER: "wrong"})
        assert response.status_code == 403

    def test_a_foreign_origin_is_rejected(self, client, logged_in):
        response = client.post(
            "/api/v1/auth/logout",
            headers={CSRF_HEADER: logged_in.csrf, "Origin": "https://evil.com"},
        )
        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "csrf_failed"

    def test_the_apps_own_origin_is_accepted(self, client, logged_in):
        response = client.post(
            "/api/v1/auth/logout",
            headers={CSRF_HEADER: logged_in.csrf, "Origin": "http://localhost"},
        )
        assert response.status_code == 204


class TestCsrfHookIsBlueprintWide:
    """The hook is registered on the blueprint, so later phases inherit it.

    Coverage is asserted through registration plus the one mutating route that
    exists today. It cannot be shown via, say, POST /api/v1/commands: Flask matches
    the URL before dispatching, and a 405 has no matched blueprint, so
    blueprint-scoped before_request handlers never run. That is harmless here --
    a 405 executes no handler and mutates nothing -- but it does mean the guard
    protects registered routes, not arbitrary method mismatches.
    """

    def test_the_hook_is_registered_for_the_whole_api_blueprint(self, app):
        from website.api.guard import enforce_csrf

        assert enforce_csrf in app.before_request_funcs["api"]

    def test_the_cache_hook_is_registered_for_the_whole_api_blueprint(self, app):
        from website.api.guard import no_store_for_authenticated

        assert no_store_for_authenticated in app.after_request_funcs["api"]

    def test_it_fires_for_a_registered_mutating_route(self, client, logged_in):
        assert client.post("/api/v1/auth/logout").status_code == 403

    def test_safe_methods_are_untouched(self, client, logged_in):
        assert client.get("/api/v1/commands").status_code == 200

    def test_no_session_means_no_csrf_requirement(self, client):
        """Unauthenticated mutations are rejected by the endpoint, not the hook."""
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 204


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
