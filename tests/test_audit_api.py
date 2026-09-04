"""Tests for GET /api/v1/guilds/<id>/audit, the Phase 7 audit reader endpoint."""

import pytest

from zephyr.db import audit


def _seed(app, guild_id, actions):
    with app.app_context():
        for action in actions:
            audit.record(action, actor_id="900", guild_id=guild_id, source="web",
                         database_url=app.config["DATABASE_URL"])


class TestAccess:
    def test_unauthenticated(self, client):
        assert client.get("/api/v1/guilds/1/audit").status_code == 401

    def test_a_guild_the_user_does_not_manage_is_403(self, app, client, logged_in):
        assert client.get("/api/v1/guilds/424242/audit").status_code == 403

    def test_a_non_numeric_id_is_400(self, app, client, logged_in):
        assert client.get("/api/v1/guilds/nope/audit").status_code == 400


class TestReading:
    def test_returns_the_guilds_entries_newest_first(self, app, client, logged_in):
        _seed(app, "1", ["first", "second", "third"])
        body = client.get("/api/v1/guilds/1/audit").get_json()
        assert body["id"] == "1"
        assert [e["action"] for e in body["entries"]] == ["third", "second", "first"]
        assert body["next_before"] is None

    def test_does_not_leak_another_guilds_log(self, app, client, logged_in):
        _seed(app, "1", ["mine"])
        _seed(app, "2", ["theirs"])
        body = client.get("/api/v1/guilds/1/audit").get_json()
        assert [e["action"] for e in body["entries"]] == ["mine"]

    def test_empty_log_is_an_empty_list(self, app, client, logged_in):
        body = client.get("/api/v1/guilds/1/audit").get_json()
        assert body["entries"] == [] and body["next_before"] is None


class TestPaging:
    def test_limit_and_before_cursor(self, app, client, logged_in):
        _seed(app, "1", [f"a{n}" for n in range(5)])
        first = client.get("/api/v1/guilds/1/audit?limit=2").get_json()
        assert len(first["entries"]) == 2
        cursor = first["next_before"]
        assert cursor is not None
        second = client.get(f"/api/v1/guilds/1/audit?limit=2&before={cursor}").get_json()
        first_ids = {e["id"] for e in first["entries"]}
        second_ids = {e["id"] for e in second["entries"]}
        assert first_ids.isdisjoint(second_ids)

    def test_a_non_numeric_limit_is_400(self, app, client, logged_in):
        response = client.get("/api/v1/guilds/1/audit?limit=lots")
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_query"

    def test_a_non_numeric_before_is_400(self, app, client, logged_in):
        assert client.get("/api/v1/guilds/1/audit?before=abc").status_code == 400


class TestCaching:
    def test_not_cacheable_by_shared_caches(self, app, client, logged_in):
        response = client.get("/api/v1/guilds/1/audit")
        assert response.headers["Cache-Control"] == "no-store, private"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestActorNames:
    """A3: the log stores an actor_id and nothing else, so every row read as a
    19-digit number. The bot is the only thing that can name one."""

    def _seed(self, db_url):
        from zephyr.db import audit

        audit.record(
            guild_id="1",
            actor_id="900000000000000001",
            action="settings.update",
            payload={"prefix": "z!"},
            database_url=db_url,
        )

    def test_names_come_back_keyed_by_id(self, client, logged_in, fake_redis, db_url):
        self._seed(db_url)

        def responder(channel, raw):
            import json

            from zephyr.services import bridge

            if channel != bridge.COMMAND_CHANNEL:
                return
            command = json.loads(raw)
            assert command["action"] == "meta.members"
            # Asked once for the distinct set, not once per row.
            assert command["args"]["ids"] == ["900000000000000001"]
            bridge.publish_response(
                command["id"],
                ok=True,
                data={"members": [{"id": "900000000000000001", "name": "Mist", "avatar_url": None}]},
            )

        fake_redis.on_publish = responder
        body = client.get("/api/v1/guilds/1/audit").get_json()
        assert body["actors"]["900000000000000001"]["name"] == "Mist"

    def test_an_unreachable_bot_leaves_the_ids_standing(self, client, logged_in, fake_redis, db_url):
        """A name lookup failing must not turn the audit log into a 503."""
        self._seed(db_url)
        # No responder: the bridge call times out.
        response = client.get("/api/v1/guilds/1/audit")
        assert response.status_code == 200
        body = response.get_json()
        assert body["actors"] == {}
        assert body["entries"][0]["actor_id"] == "900000000000000001"

    def test_no_bridge_call_at_all_when_nothing_has_an_actor(self, client, logged_in, fake_redis, db_url):
        calls = []
        fake_redis.on_publish = lambda channel, raw: calls.append(channel)
        body = client.get("/api/v1/guilds/1/audit").get_json()
        assert body["actors"] == {}
        assert calls == []


class TestFilterQuery:
    """F4's API half: allow-listed query parameters, not free text."""

    def _seed(self, db_url):
        from zephyr.db import audit

        audit.record(guild_id="1", actor_id="900000000000000001", action="player.volume", source="web", database_url=db_url)
        audit.record(guild_id="1", actor_id="900000000000000002", action="settings.update", source="web", database_url=db_url)

    def test_action_prefix_narrows_the_page(self, client, logged_in, fake_redis, db_url):
        self._seed(db_url)
        body = client.get("/api/v1/guilds/1/audit?action=player").get_json()
        assert [entry["action"] for entry in body["entries"]] == ["player.volume"]

    def test_actor_id_must_be_an_id(self, client, logged_in, fake_redis, db_url):
        response = client.get("/api/v1/guilds/1/audit?actor_id=not-an-id")
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_query"

    def test_source_is_allow_listed(self, client, logged_in, fake_redis, db_url):
        # Anything else is a client bug, not something to hand to a WHERE.
        assert client.get("/api/v1/guilds/1/audit?source=web").status_code == 200
        response = client.get("/api/v1/guilds/1/audit?source=carrier-pigeon")
        assert response.status_code == 400

    def test_an_over_long_action_is_truncated_not_rejected(self, client, logged_in, fake_redis, db_url):
        # A 4KB LIKE pattern is a table scan; a truncated one is simply a filter
        # that matches nothing, which is the honest answer to a nonsense query.
        response = client.get(f"/api/v1/guilds/1/audit?action={'x' * 500}")
        assert response.status_code == 200
        assert response.get_json()["entries"] == []
