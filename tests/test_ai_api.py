"""The dashboard's AI memory endpoints, and the half of a purge that lives in the bot.

Deleting the stored row is only half a purge: the bot keeps a per-guild rolling
buffer and falls back to it whenever a channel has no row, so without the bridge
call the next message re-remembers what the dashboard just deleted.  That call is
best-effort by design, which is why the offline case below still expects a 204.
"""

import json

from zephyr.db import ai as ai_db
from zephyr.services import bridge


def _headers(logged_in):
    return {"X-Zephyr-CSRF": logged_in.csrf}


def _answering_bot(fake_redis, seen):
    """Stand in for a running bot: record each command and answer it."""

    def responder(channel, raw):
        if channel != bridge.COMMAND_CHANNEL:
            return
        command = json.loads(raw)
        seen.append(command)
        bridge.publish_response(command["id"], ok=True, data={"cleared": True})

    fake_redis.on_publish = responder


class TestPurgeMemory:
    def test_a_purge_also_asks_the_bot_to_drop_its_buffer(self, client, logged_in, fake_redis, db_url):
        ai_db.append_exchange("10", "1", "question", "answer", database_url=db_url)
        seen = []
        _answering_bot(fake_redis, seen)

        response = client.delete("/api/v1/guilds/1/ai/memory/10", headers=_headers(logged_in))

        assert response.status_code == 204
        assert ai_db.load_conversation("10", database_url=db_url) is None
        assert [command["action"] for command in seen] == ["ai.memory.purge"]
        assert seen[0]["guild_id"] == "1"
        assert seen[0]["args"] == {"channel_id": "10"}

    def test_an_offline_bot_does_not_undo_a_completed_purge(self, client, logged_in, fake_redis, db_url):
        """Nothing answers the bridge, but the row is already gone, so this is a 204."""
        ai_db.append_exchange("10", "1", "question", "answer", database_url=db_url)

        assert client.delete("/api/v1/guilds/1/ai/memory/10", headers=_headers(logged_in)).status_code == 204
        assert ai_db.load_conversation("10", database_url=db_url) is None

    def test_another_guilds_channel_is_not_purgeable(self, client, logged_in, fake_redis, db_url):
        ai_db.append_exchange("10", "2", "question", "answer", database_url=db_url)

        assert client.delete("/api/v1/guilds/1/ai/memory/10", headers=_headers(logged_in)).status_code == 404
        assert ai_db.load_conversation("10", database_url=db_url) is not None

    def test_a_dm_channel_is_not_reachable_from_the_dashboard(self, client, logged_in, fake_redis, db_url):
        """A NULL guild_id row must stay invisible to a guild-scoped caller."""
        ai_db.append_exchange("11", None, "question", "answer", database_url=db_url)

        assert client.delete("/api/v1/guilds/1/ai/memory/11", headers=_headers(logged_in)).status_code == 404
        assert ai_db.load_conversation("11", database_url=db_url) is not None

    def test_a_purge_is_recorded_in_the_audit_log(self, client, logged_in, fake_redis, db_url):
        from zephyr.db import audit

        ai_db.append_exchange("10", "1", "question", "answer", database_url=db_url)
        _answering_bot(fake_redis, [])

        client.delete("/api/v1/guilds/1/ai/memory/10", headers=_headers(logged_in))

        entries = audit.read("1", database_url=db_url)["entries"]
        assert [entry["action"] for entry in entries] == ["ai.memory.purge"]
        assert entries[0]["source"] == "web"
