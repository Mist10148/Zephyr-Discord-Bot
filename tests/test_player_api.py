"""The player endpoints and the bridge's error mapping.

The three bridge failure modes are genuinely different -- no Redis, no answer,
and a refusal -- and the whole point of these tests is that they stay
distinguishable to the client.
"""

import json

import pytest

from zephyr.services import bridge


def _answer(fake_redis, *, ok=True, data=None, error=None):
    """Stand in for the bot, replying the instant a command is published."""

    def responder(channel, raw):
        if channel != bridge.COMMAND_CHANNEL:
            return
        bridge.publish_response(json.loads(raw)["id"], ok=ok, data=data, error=error)

    fake_redis.on_publish = responder


def _headers(logged_in):
    return {"X-Zephyr-CSRF": logged_in.csrf}


class TestGetPlayer:
    def test_a_published_snapshot_comes_back_marked_live(self, client, logged_in, fake_redis):
        bridge.write_player_snapshot("1", {"connected": True, "track": {"title": "A"}, "queue": []})

        body = client.get("/api/v1/guilds/1/player").get_json()

        assert body["live"] is True
        assert body["track"]["title"] == "A"

    def test_no_snapshot_is_not_playing_rather_than_an_error(self, client, logged_in, fake_redis):
        """The bot being offline is a state the dashboard renders, not a failure."""
        response = client.get("/api/v1/guilds/1/player")

        assert response.status_code == 200
        assert response.get_json() == {
            "guild_id": "1", "live": False, "connected": False, "track": None, "queue": []
        }

    def test_an_expired_snapshot_reads_as_not_live(self, client, logged_in, fake_redis):
        bridge.write_player_snapshot("1", {"connected": True})
        fake_redis.expire_now(bridge.PLAYER_KEY.format(guild_id="1"))

        assert client.get("/api/v1/guilds/1/player").get_json()["live"] is False

    def test_a_guild_you_do_not_manage_is_forbidden(self, client, logged_in, fake_redis):
        assert client.get("/api/v1/guilds/999/player").status_code == 403

    def test_signing_out_is_a_401(self, client, fake_redis):
        assert client.get("/api/v1/guilds/1/player").status_code == 401


class TestPostPlayerAction:
    def test_an_action_reaches_the_bot_with_the_actor_attached(self, client, logged_in, fake_redis):
        seen = {}

        def responder(channel, raw):
            if channel == bridge.COMMAND_CHANNEL:
                seen.update(json.loads(raw))
                bridge.publish_response(seen["id"], ok=True, data={"skipped": "A"})

        fake_redis.on_publish = responder
        response = client.post("/api/v1/guilds/1/player/skip", headers=_headers(logged_in))

        assert response.status_code == 200
        assert response.get_json() == {"skipped": "A"}
        assert seen["action"] == "player.skip"
        assert seen["actor_id"] == logged_in.user_id

    def test_a_refusal_is_409_and_keeps_the_bots_own_reason(self, client, logged_in, fake_redis):
        """Retrying will never help, and the bot's message is the true one."""
        _answer(fake_redis, ok=False, error="Join the voice channel Zephyr is in.")

        response = client.post("/api/v1/guilds/1/player/skip", headers=_headers(logged_in))

        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "bot_refused"
        assert "voice channel" in response.get_json()["error"]["message"]

    def test_no_answer_is_504_not_a_hang(self, client, logged_in, fake_redis, monkeypatch):
        monkeypatch.setattr(bridge, "COMMAND_TIMEOUT", 0.05)

        response = client.post("/api/v1/guilds/1/player/skip", headers=_headers(logged_in))

        assert response.status_code == 504
        assert response.get_json()["error"]["code"] == "bot_unreachable"

    def test_a_deployment_without_redis_says_so(self, app, client, logged_in, fake_redis):
        app.config["REDIS_URL"] = None
        response = client.post("/api/v1/guilds/1/player/skip", headers=_headers(logged_in))
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "bridge_not_configured"

    def test_an_action_the_ui_never_offers_is_refused_before_the_bridge(self, client, logged_in, fake_redis):
        """The bridge is a general command channel; this endpoint is not."""
        fake_redis.on_publish = lambda channel, raw: pytest.fail("nothing should be published")

        response = client.post("/api/v1/guilds/1/player/teleport", headers=_headers(logged_in))

        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "unknown_action"

    def test_unknown_arguments_are_rejected_rather_than_dropped(self, client, logged_in, fake_redis):
        response = client.post(
            "/api/v1/guilds/1/player/volume",
            json={"volume": 50, "shard": 3},
            headers=_headers(logged_in),
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "unknown_fields"

    def test_a_missing_csrf_token_is_rejected(self, client, logged_in, fake_redis):
        assert client.post("/api/v1/guilds/1/player/skip").status_code == 403

    def test_the_player_is_rate_limited_per_session(self, client, logged_in, fake_redis):
        from website.api.player import PLAYER_RATE_LIMIT

        _answer(fake_redis, data={})
        statuses = [
            client.post("/api/v1/guilds/1/player/skip", headers=_headers(logged_in)).status_code
            for _ in range(PLAYER_RATE_LIMIT + 3)
        ]

        assert statuses[:PLAYER_RATE_LIMIT] == [200] * PLAYER_RATE_LIMIT
        assert statuses[-1] == 429

    def test_a_rate_limit_counter_failure_does_not_lock_the_player(self, client, logged_in, fake_redis, monkeypatch):
        """A Redis blip must not present as "you are doing that too much"."""
        _answer(fake_redis, data={})
        monkeypatch.setattr(fake_redis, "incr", lambda key: (_ for _ in ()).throw(RuntimeError("down")), raising=False)

        assert client.post("/api/v1/guilds/1/player/skip", headers=_headers(logged_in)).status_code == 200

    def test_a_successful_action_is_audited(self, app, client, logged_in, fake_redis):
        from sqlalchemy import select

        from zephyr.db.models import AuditLog
        from zephyr.db.session import get_engine

        _answer(fake_redis, data={})
        client.post("/api/v1/guilds/1/player/pause", headers=_headers(logged_in))

        with get_engine(app.config["DATABASE_URL"]).connect() as connection:
            rows = connection.execute(select(AuditLog.action, AuditLog.source, AuditLog.guild_id)).all()
        assert [(row.action, row.source, row.guild_id) for row in rows] == [("player.pause", "web", "1")]

    def test_a_refused_action_is_not_audited(self, app, client, logged_in, fake_redis):
        """Rejected button presses would drown the log in people who simply
        were not in the voice channel."""
        from sqlalchemy import select

        from zephyr.db.models import AuditLog
        from zephyr.db.session import get_engine

        _answer(fake_redis, ok=False, error="nope")
        client.post("/api/v1/guilds/1/player/pause", headers=_headers(logged_in))

        with get_engine(app.config["DATABASE_URL"]).connect() as connection:
            assert connection.execute(select(AuditLog.id)).all() == []


class TestStatus:
    def test_a_heartbeat_makes_the_bot_online(self, client, fake_redis):
        bridge.write_presence({"online": True, "guild_count": 4, "latency_ms": 30, "uptime_s": 90})

        body = client.get("/api/v1/status").get_json()

        assert body["bot"]["online"] is True
        assert body["bot"]["guild_count"] == 4

    def test_silence_is_offline(self, client, fake_redis):
        assert client.get("/api/v1/status").get_json()["bot"]["online"] is False

    def test_an_expired_heartbeat_is_offline(self, client, fake_redis):
        bridge.write_presence({"online": True, "guild_count": 4})
        fake_redis.expire_now(bridge.PRESENCE_KEY)

        assert client.get("/api/v1/status").get_json()["bot"]["online"] is False

    def test_a_shutdown_heartbeat_is_offline(self, client, fake_redis):
        """close() publishes online:false so a clean stop shows immediately."""
        bridge.write_presence({"online": False})
        assert client.get("/api/v1/status").get_json()["bot"]["online"] is False

    def test_a_deployment_without_redis_reports_offline_not_an_error(self, public_app, fake_redis):
        response = public_app.test_client().get("/api/v1/status")
        assert response.status_code == 200
        assert response.get_json()["bot"]["online"] is False


class TestGuildMeta:
    def test_channels_and_roles_come_from_the_bot(self, client, logged_in, fake_redis):
        _answer(fake_redis, data={"channels": [{"id": "5", "name": "general", "can_send": True}], "roles": []})

        body = client.get("/api/v1/guilds/1/meta").get_json()

        assert body["channels"][0]["name"] == "general"

    def test_an_offline_bot_is_504(self, client, logged_in, fake_redis, monkeypatch):
        monkeypatch.setattr(bridge, "COMMAND_TIMEOUT", 0.05)
        assert client.get("/api/v1/guilds/1/meta").status_code == 504
