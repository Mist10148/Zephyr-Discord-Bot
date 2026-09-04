"""Tests for GET /api/v1/guilds/<id>, the read-only guild overview."""

import json
import time

import pytest

from website.session import create_session
from zephyr.services.bridge import GUILDS_KEY, GUILDS_UPDATED_KEY
from zephyr.config import COMMAND_PREFIX

MANAGED = {"id": "1", "name": "Managed Guild", "icon": "icon1", "owner": True}


def _sign_in(app, client, guilds=(MANAGED,)):
    with app.app_context():
        session = create_session(
            {"id": "900000000000000001", "username": "tester", "global_name": None, "avatar": None},
            list(guilds),
            ttl=app.config["AUTH_SESSION_TTL"],
            redis_url=app.config["REDIS_URL"],
        )
    client.set_cookie(app.config["AUTH_COOKIE_NAME"], session.sid, domain="localhost")
    return session


def _publish(fake_redis, ids):
    fake_redis.set(GUILDS_KEY, json.dumps({str(i): {"id": str(i)} for i in ids}))
    fake_redis.set(GUILDS_UPDATED_KEY, str(int(time.time())))


def _seed_settings(app, **values):
    from sqlalchemy import insert

    from zephyr.db.models import Guild
    from zephyr.db.session import get_engine

    engine = get_engine(app.config["DATABASE_URL"])
    with engine.begin() as connection:
        connection.execute(insert(Guild.__table__).values(**values))


class TestAccess:
    def test_unauthenticated(self, client):
        response = client.get("/api/v1/guilds/1")
        assert response.status_code == 401
        assert response.get_json()["error"]["code"] == "unauthenticated"

    def test_a_guild_the_user_does_not_manage_is_403(self, app, client):
        """A UX check; the bot re-validates permissions before acting."""
        _sign_in(app, client)
        response = client.get("/api/v1/guilds/424242")
        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "forbidden"

    def test_403_is_distinct_from_401(self, app, client):
        """The client must not treat missing access as a signed-out state."""
        _sign_in(app, client)
        assert client.get("/api/v1/guilds/424242").status_code == 403

    def test_a_non_numeric_id_is_400(self, app, client):
        _sign_in(app, client)
        response = client.get("/api/v1/guilds/not-an-id")
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_guild_id"

    def test_unconfigured_deployments_answer_503(self, public_app):
        response = public_app.test_client().get("/api/v1/guilds/1")
        assert response.status_code == 503


class TestDefaults:
    def test_an_unconfigured_guild_reports_documented_defaults(self, app, client):
        _sign_in(app, client)
        body = client.get("/api/v1/guilds/1").get_json()
        assert body["defaults_applied"] is True
        # Read from config rather than hardcoded, so the dashboard and the bot
        # cannot disagree about what "unconfigured" means. It was "/", which is
        # the value the bot stopped using because it collided with the slash
        # surface.
        assert body["prefix"] == COMMAND_PREFIX
        assert COMMAND_PREFIX != "/"
        assert body["locale"] == "en"
        assert body["timezone"] == "UTC"
        assert body["default_volume"] == 50
        assert body["dj_role_id"] is None
        assert body["music_channel_ids"] == []

    def test_default_enabled_cogs_come_from_config(self, app, client):
        _sign_in(app, client)
        body = client.get("/api/v1/guilds/1").get_json()
        assert body["enabled_cogs"] == list(app.config["ENABLED_COGS"])

    def test_identity_comes_from_the_session(self, app, client):
        _sign_in(app, client)
        body = client.get("/api/v1/guilds/1").get_json()
        assert body["id"] == "1"
        assert body["name"] == "Managed Guild"
        assert body["icon"] == "icon1"
        assert body["icon_url"].endswith("/icons/1/icon1.png?size=128")
        assert body["owner"] is True

    def test_marked_editable_now_that_patch_exists(self, app, client):
        """Phase 3 shipped this false because nothing could write. PATCH
        /guilds/<id>/settings is that writer, so the flag has to follow."""
        _sign_in(app, client)
        assert client.get("/api/v1/guilds/1").get_json()["editable"] is True


class TestStoredSettings:
    def test_a_seeded_row_wins_over_the_defaults(self, app, client):
        _sign_in(app, client)
        _seed_settings(
            app,
            id="1",
            prefix="!",
            locale="fil",
            timezone="Asia/Manila",
            default_volume=80,
            dj_role_id="555",
            music_channel_ids=["777"],
            enabled_cogs=["weather", "music"],
        )
        body = client.get("/api/v1/guilds/1").get_json()
        assert body["defaults_applied"] is False
        assert body["prefix"] == "!"
        assert body["locale"] == "fil"
        assert body["timezone"] == "Asia/Manila"
        assert body["default_volume"] == 80
        assert body["dj_role_id"] == "555"
        assert body["music_channel_ids"] == ["777"]
        assert body["enabled_cogs"] == ["weather", "music"]

    def test_null_columns_fall_back_to_the_defaults(self, app, client):
        """A partially configured row must not blank out the rest."""
        _sign_in(app, client)
        _seed_settings(app, id="1", prefix="?")
        body = client.get("/api/v1/guilds/1").get_json()
        assert body["defaults_applied"] is False
        assert body["prefix"] == "?"
        assert body["timezone"] == "UTC"
        assert body["enabled_cogs"] == list(app.config["ENABLED_COGS"])

    def test_a_row_for_another_guild_is_not_used(self, app, client):
        _sign_in(app, client)
        _seed_settings(app, id="999", prefix="!")
        assert client.get("/api/v1/guilds/1").get_json()["prefix"] == COMMAND_PREFIX


class TestBotPresence:
    def test_true_when_published(self, app, client, fake_redis):
        _sign_in(app, client)
        _publish(fake_redis, ["1"])
        body = client.get("/api/v1/guilds/1").get_json()
        assert body["bot_present"] is True
        assert body["bot_snapshot_at"] is not None

    def test_false_when_the_bot_was_removed(self, app, client, fake_redis):
        """A bot kicked between page loads is a real, displayable state."""
        _sign_in(app, client)
        _publish(fake_redis, ["2"])
        assert client.get("/api/v1/guilds/1").get_json()["bot_present"] is False

    def test_unknown_without_a_snapshot(self, app, client, fake_redis):
        _sign_in(app, client)
        body = client.get("/api/v1/guilds/1").get_json()
        assert body["bot_present"] is None
        assert body["bot_snapshot_at"] is None

    def test_still_served_when_the_bot_is_absent(self, app, client, fake_redis):
        _sign_in(app, client)
        _publish(fake_redis, [])
        assert client.get("/api/v1/guilds/1").status_code == 200


class TestCaching:
    def test_not_cacheable_by_shared_caches(self, app, client):
        _sign_in(app, client)
        response = client.get("/api/v1/guilds/1")
        assert response.headers["Cache-Control"] == "no-store, private"
        assert "Cookie" in response.headers["Vary"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
