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
