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


class TestHistory:
    def test_history_can_search_and_edit_with_revisions(self, client, logged_in, db_url):
        from zephyr.db import audit

        ai_db.append_exchange("12", "1", "original question", "answer", database_url=db_url)
        response = client.get("/api/v1/guilds/1/ai/history?q=original", headers=_headers(logged_in))
        assert response.status_code == 200
        assert response.get_json()["entries"][0]["channel_id"] == "12"

        detail = client.get("/api/v1/guilds/1/ai/history/12", headers=_headers(logged_in)).get_json()
        message = detail["messages"][0]
        edited = client.patch(
            f"/api/v1/guilds/1/ai/history/12/messages/{message['id']}",
            headers=_headers(logged_in),
            json={"content": "corrected question", "expected_version": 1, "reason": "Typo"},
        )
        assert edited.status_code == 200
        assert edited.get_json()["version"] == 2

        revisions = client.get(
            f"/api/v1/guilds/1/ai/history/12/messages/{message['id']}/revisions",
            headers=_headers(logged_in),
        )
        assert revisions.status_code == 200
        assert revisions.get_json()["revisions"][0]["previous_content"] == "original question"
        event = audit.read("1", database_url=db_url)["entries"][0]
        assert event["action"] == "ai.history.message.edit"
        assert "content" not in event["payload"]

    def test_history_mutation_requires_csrf(self, client, logged_in, db_url):
        ai_db.append_exchange("13", "1", "question", "answer", database_url=db_url)
        response = client.patch(
            "/api/v1/guilds/1/ai/history/13",
            json={"is_archived": True},
        )
        assert response.status_code == 403

    def test_history_cannot_edit_another_guilds_message(self, client, logged_in, db_url):
        ai_db.append_exchange("14", "2", "private", "answer", database_url=db_url)
        detail = ai_db.load_history("14", "2", database_url=db_url)
        message_id = detail["messages"][0]["id"]
        response = client.patch(
            f"/api/v1/guilds/1/ai/history/14/messages/{message_id}",
            headers=_headers(logged_in),
            json={"content": "changed", "expected_version": 1},
        )
        assert response.status_code == 404

    def test_history_labels_and_annotations_are_audited(self, client, logged_in, db_url):
        from zephyr.db import audit

        ai_db.append_exchange("15", "1", "question", "answer", database_url=db_url)
        label = client.post(
            "/api/v1/guilds/1/ai/history/15/labels",
            headers=_headers(logged_in),
            json={"label": "Needs review"},
        )
        annotation = client.post(
            "/api/v1/guilds/1/ai/history/15/annotations",
            headers=_headers(logged_in),
            json={"note": "Ask the moderator to follow up."},
        )
        assert label.status_code == 201
        assert annotation.status_code == 201
        detail = client.get("/api/v1/guilds/1/ai/history/15", headers=_headers(logged_in)).get_json()
        assert detail["labels"][0]["label"] == "needs review"
        assert detail["annotations"][0]["note"].startswith("Ask")
        assert [entry["action"] for entry in audit.read("1", database_url=db_url)["entries"][:2]] == [
            "ai.history.annotation.add",
            "ai.history.label.add",
        ]


class TestPrivateDMHistory:
    def test_only_the_signed_in_owner_can_list_and_open_dm_history(self, client, logged_in, db_url):
        ai_db.append_exchange("80", None, "my private question", "answer", owner_id=logged_in.user_id, database_url=db_url)
        ai_db.append_exchange("81", None, "another private question", "answer", owner_id="800", database_url=db_url)

        listing = client.get("/api/v1/me/ai/history")
        assert listing.status_code == 200
        assert [entry["channel_id"] for entry in listing.get_json()["entries"]] == ["80"]
        assert client.get("/api/v1/me/ai/history/80").status_code == 200
        assert client.get("/api/v1/me/ai/history/81").status_code == 404

    def test_dm_message_edit_and_revision_are_owner_scoped(self, client, logged_in, db_url):
        ai_db.append_exchange("82", None, "original", "answer", owner_id=logged_in.user_id, database_url=db_url)
        message_id = ai_db.load_dm_history("82", logged_in.user_id, database_url=db_url)["messages"][0]["id"]
        response = client.patch(
            f"/api/v1/me/ai/history/82/messages/{message_id}",
            headers=_headers(logged_in),
            json={"content": "corrected", "expected_version": 1},
        )
        assert response.status_code == 200
        revisions = client.get(f"/api/v1/me/ai/history/82/messages/{message_id}/revisions")
        assert revisions.status_code == 200
        assert revisions.get_json()["revisions"][0]["previous_content"] == "original"

    def test_dm_purge_requires_csrf_and_deletes_only_owned_history(self, client, logged_in, fake_redis, db_url):
        ai_db.append_exchange("83", None, "private", "answer", owner_id=logged_in.user_id, database_url=db_url)
        assert client.delete("/api/v1/me/ai/history/83").status_code == 403
        assert client.delete("/api/v1/me/ai/history/83", headers=_headers(logged_in)).status_code == 204
        assert ai_db.load_dm_history("83", logged_in.user_id, database_url=db_url) is None
