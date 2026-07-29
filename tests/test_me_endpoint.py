"""Tests for GET /api/v1/me.

Redis is the in-memory double from conftest and the database is a temporary SQLite
file, so no network calls happen.
"""

import json
import time

import pytest

from website.session import SESSION_PREFIX, create_session
from zephyr.services.bridge import GUILDS_KEY, GUILDS_UPDATED_KEY


def _sign_in(app, client, guilds, user_id="900000000000000001"):
    with app.app_context():
        session = create_session(
            {"id": user_id, "username": "tester", "global_name": "Tester", "avatar": "avhash"},
            guilds,
            ttl=app.config["AUTH_SESSION_TTL"],
            redis_url=app.config["REDIS_URL"],
        )
    client.set_cookie(app.config["AUTH_COOKIE_NAME"], session.sid, domain="localhost")
    return session


def _publish(fake_redis, ids, updated_at=None):
    fake_redis.set(GUILDS_KEY, json.dumps({str(i): {"id": str(i)} for i in ids}))
    fake_redis.set(GUILDS_UPDATED_KEY, str(updated_at or int(time.time())))


class TestAuthentication:
    def test_no_cookie_is_401_with_the_standard_envelope(self, client):
        response = client.get("/api/v1/me")
        assert response.status_code == 401
        assert response.get_json()["error"]["code"] == "unauthenticated"

    def test_never_a_redirect(self, client):
        """fetch follows redirects, so a 302 to Discord would surface as status 0."""
        assert client.get("/api/v1/me").status_code == 401

    def test_a_cookie_whose_session_is_gone_is_401(self, app, client, fake_redis):
        session = _sign_in(app, client, [])
        fake_redis.delete(SESSION_PREFIX + session.sid)
        assert client.get("/api/v1/me").status_code == 401

    def test_an_expired_session_is_401(self, app, client, fake_redis):
        session = _sign_in(app, client, [])
        fake_redis.expire_now(SESSION_PREFIX + session.sid)
        assert client.get("/api/v1/me").status_code == 401

    def test_a_redis_outage_is_503_not_401(self, app, client, fake_redis):
        """A blip must not be indistinguishable from being signed out."""
        _sign_in(app, client, [])
        fake_redis.raise_on = ConnectionError("redis is down")
        response = client.get("/api/v1/me")
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "session_store_unavailable"

    def test_unconfigured_deployments_answer_503(self, public_app):
        response = public_app.test_client().get("/api/v1/me")
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "auth_not_configured"


class TestPayload:
    def test_user_block(self, app, client):
        _sign_in(app, client, [])
        body = client.get("/api/v1/me").get_json()
        assert body["user"]["id"] == "900000000000000001"
        assert body["user"]["username"] == "tester"
        assert body["user"]["global_name"] == "Tester"
        assert body["user"]["avatar"] == "avhash"
        assert body["user"]["avatar_url"].endswith("/avatars/900000000000000001/avhash.png?size=128")

    def test_a_user_without_an_avatar_gets_the_default(self, app, client):
        with app.app_context():
            from website.session import create_session

            session = create_session(
                {"id": str(1 << 22), "username": "plain", "global_name": None, "avatar": None},
                [],
                ttl=app.config["AUTH_SESSION_TTL"],
                redis_url=app.config["REDIS_URL"],
            )
        client.set_cookie(app.config["AUTH_COOKIE_NAME"], session.sid, domain="localhost")
        body = client.get("/api/v1/me").get_json()
        assert body["user"]["avatar"] is None
        assert body["user"]["avatar_url"].endswith("/embed/avatars/1.png")

    def test_ids_are_strings(self, app, client, logged_in):
        """Snowflakes exceed Number.MAX_SAFE_INTEGER in JavaScript."""
        body = client.get("/api/v1/me").get_json()
        assert isinstance(body["user"]["id"], str)
        assert all(isinstance(guild["id"], str) for guild in body["guilds"])

    def test_exposes_the_csrf_token(self, app, client, logged_in):
        assert client.get("/api/v1/me").get_json()["csrf_token"] == logged_in.csrf

    def test_no_permissions_bitfield_is_leaked(self, app, client):
        """Web-side permission maths is not the frontend's job."""
        _sign_in(app, client, [{"id": "1", "name": "One", "icon": None, "owner": True}])
        body = client.get("/api/v1/me").get_json()
        assert "permissions" not in body["guilds"][0]

    def test_invite_url_carries_the_configured_client_id(self, app, client, logged_in):
        body = client.get("/api/v1/me").get_json()
        assert app.config["DISCORD_CLIENT_ID"] in body["invite_url"]
        assert app.config["DISCORD_INVITE_PERMISSIONS"] in body["invite_url"]

    def test_icon_url_is_none_without_an_icon(self, app, client):
        _sign_in(app, client, [{"id": "1", "name": "One", "icon": None, "owner": True}])
        assert client.get("/api/v1/me").get_json()["guilds"][0]["icon_url"] is None

    def test_icon_url_is_built_when_an_icon_exists(self, app, client, logged_in):
        guild = client.get("/api/v1/me").get_json()["guilds"][0]
        assert guild["icon_url"].endswith("/icons/1/icon1.png?size=128")

    def test_is_not_cacheable_by_shared_caches(self, app, client, logged_in):
        """Otherwise a proxy could serve one user's CSRF token to another."""
        response = client.get("/api/v1/me")
        assert response.headers["Cache-Control"] == "no-store, private"
        assert "Cookie" in response.headers["Vary"]


class TestBotPresence:
    def test_true_when_the_snapshot_lists_the_guild(self, app, client, fake_redis):
        _sign_in(app, client, [{"id": "1", "name": "One", "icon": None, "owner": True}])
        _publish(fake_redis, ["1"])
        body = client.get("/api/v1/me").get_json()
        assert body["guilds"][0]["bot_present"] is True
        assert body["bot_snapshot_at"] is not None

    def test_false_when_the_snapshot_omits_the_guild(self, app, client, fake_redis):
        _sign_in(app, client, [{"id": "1", "name": "One", "icon": None, "owner": True}])
        _publish(fake_redis, ["999"])
        assert client.get("/api/v1/me").get_json()["guilds"][0]["bot_present"] is False

    def test_a_guild_without_the_bot_is_still_listed(self, app, client, fake_redis):
        """Hiding a server the user administers is an unexplainable dead end."""
        _sign_in(app, client, [{"id": "1", "name": "One", "icon": None, "owner": True}])
        _publish(fake_redis, [])
        body = client.get("/api/v1/me").get_json()
        assert [guild["id"] for guild in body["guilds"]] == ["1"]

    def test_unknown_when_nothing_was_ever_published(self, app, client, fake_redis):
        """None, not False: unknown and absent must render differently."""
        _sign_in(app, client, [{"id": "1", "name": "One", "icon": None, "owner": True}])
        body = client.get("/api/v1/me").get_json()
        assert body["guilds"][0]["bot_present"] is None
        assert body["bot_snapshot_at"] is None

    def test_a_snapshot_read_failure_degrades_instead_of_failing(self, app, client, fake_redis, monkeypatch):
        _sign_in(app, client, [{"id": "1", "name": "One", "icon": None, "owner": True}])

        def explode(**_kwargs):
            raise ConnectionError("redis is down")

        monkeypatch.setattr("zephyr.services.bridge.read_guild_snapshot", explode)
        response = client.get("/api/v1/me")
        assert response.status_code == 200
        assert response.get_json()["guilds"][0]["bot_present"] is None


class TestGuildStaleness:
    def test_fresh_after_signing_in(self, app, client, logged_in):
        assert client.get("/api/v1/me").get_json()["guilds_stale"] is False

    def test_stale_once_older_than_the_freshness_window(self, app, client, fake_redis):
        session = _sign_in(app, client, [])
        key = SESSION_PREFIX + session.sid
        stored = json.loads(fake_redis.get(key))
        stored["guilds_fetched_at"] = int(time.time()) - (app.config["GUILDS_FRESH_SECONDS"] + 60)
        fake_redis.set(key, json.dumps(stored), ex=app.config["AUTH_SESSION_TTL"])
        assert client.get("/api/v1/me").get_json()["guilds_stale"] is True


class TestEmptyGuildList:
    def test_a_user_who_manages_nothing_gets_an_empty_list(self, app, client):
        _sign_in(app, client, [])
        body = client.get("/api/v1/me").get_json()
        assert body["guilds"] == []
        assert body["user"]["id"] == "900000000000000001"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
