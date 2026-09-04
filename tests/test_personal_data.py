"""Exporting and deleting one person's data.

Discord requires a privacy policy for verification, and a policy describing a
deletion path has to have one. The two properties that matter most here are
negative: an export must not silently omit a store, and a deletion must not take
somebody else's data with it.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from zephyr.db import ai as ai_db
from zephyr.db import audit, personal_data, playlists
from zephyr.db import activity as activity_repo
from zephyr.db import mod_cases as mod_repo
from zephyr.db import reminders as reminders_repo
from zephyr.db.models import AIMessage, AuditLog, Playlist, PlaylistTrack
from zephyr.db.session import get_engine
from zephyr.db.weather_subs import write_bot_user

ME = "900000000000000001"
SOMEBODY_ELSE = "900000000000000002"


@pytest.fixture
def seeded(db_url):
    """One of everything, for me and for somebody else."""
    write_bot_user(ME, {"default_city": "Iloilo City", "units": "metric"}, database_url=db_url)
    write_bot_user(SOMEBODY_ELSE, {"default_city": "Cebu"}, database_url=db_url)

    playlists.save_playlist(ME, "Focus", [{"title": "A", "url": "https://y.tld/a"}], database_url=db_url)
    playlists.save_playlist(SOMEBODY_ELSE, "Theirs", [{"title": "B", "url": "https://y.tld/b"}], database_url=db_url)

    audit.record(guild_id="1", actor_id=ME, action="settings.update", payload={"prefix": "!"}, database_url=db_url)
    audit.record(guild_id="1", actor_id=SOMEBODY_ELSE, action="player.skip", database_url=db_url)

    ai_db.append_exchange("10", "1", "my question", "the answer", author_id=ME, database_url=db_url)
    ai_db.append_exchange("10", "1", "their question", "their answer", author_id=SOMEBODY_ELSE, database_url=db_url)

    for user_id, message in ((ME, "my reminder"), (SOMEBODY_ELSE, "their reminder")):
        reminders_repo.create(
            {
                "user_id": user_id, "guild_id": "1", "channel_id": "5", "message": message,
                "due_at": datetime(2026, 12, 1, tzinfo=timezone.utc), "tz": "UTC",
                "repeat_every_seconds": None, "attempts": 0, "source": "discord",
            },
            database_url=db_url,
        )
    activity_repo.flush({("1", ME): 3, ("1", SOMEBODY_ELSE): 2}, database_url=db_url)
    mod_repo.record(
        guild_id="1", action="warn", target_id=ME, moderator_id=SOMEBODY_ELSE,
        reason="told off", database_url=db_url,
    )
    return db_url


class TestExport:
    def test_it_covers_every_store(self, seeded):
        """The point of an export is that somebody can check it is complete."""
        payload = personal_data.export(ME, database_url=seeded)

        assert payload["user_id"] == ME
        assert payload["bot_preferences"]["default_city"] == "Iloilo City"
        assert [item["name"] for item in payload["playlists"]] == ["Focus"]
        assert payload["playlists"][0]["tracks"][0]["title"] == "A"
        assert [entry["action"] for entry in payload["audit_entries"]] == ["settings.update"]
        assert any("my question" in message["content"] for message in payload["ai_messages"])
        assert [row["message"] for row in payload["reminders"]] == ["my reminder"]
        assert [row["reason"] for row in payload["moderation_record"]] == ["told off"]
        assert [row["messages"] for row in payload["activity"]] == [3]

    def test_it_contains_nobody_elses_data(self, seeded):
        payload = personal_data.export(ME, database_url=seeded)
        serialised = str(payload)
        assert SOMEBODY_ELSE not in serialised
        assert "their question" not in serialised
        assert "their reminder" not in serialised
        assert "Cebu" not in serialised

    def test_a_moderation_record_never_names_the_moderator(self, seeded):
        """Naming the moderator who acted to the person they acted on invites
        retaliation, and it is not needed to check the record is accurate.

        SOMEBODY_ELSE is the moderator here, so the general "nobody else's
        data" assertion above would pass for the wrong reason if this column
        leaked -- it is asserted on its own.
        """
        record = personal_data.export(ME, database_url=seeded)["moderation_record"]

        assert record
        assert all("moderator_id" not in entry for entry in record)

    def test_it_states_what_it_cannot_include(self, seeded):
        """An export that silently omitted these would look complete and not
        be: sessions cannot be enumerated, and pre-authorship messages cannot
        be attributed to anyone."""
        notes = personal_data.export(ME, database_url=seeded)["notes"]
        assert "session id" in notes["sessions"]
        assert "cannot be attributed" in notes["ai_messages"]
        assert "several people" in notes["shared_conversations"]

    def test_an_unknown_person_gets_an_empty_export_not_an_error(self, seeded):
        payload = personal_data.export("404", database_url=seeded)
        assert payload["dashboard_account"] is None
        assert payload["bot_preferences"] is None
        assert payload["playlists"] == []

    def test_everything_is_json_able(self, seeded):
        import json

        json.dumps(personal_data.export(ME, database_url=seeded))


class TestDelete:
    def test_it_removes_my_rows(self, seeded):
        removed = personal_data.delete(ME, database_url=seeded)

        assert removed["bot_preferences"] == 1
        assert removed["playlists"] == 1
        assert removed["ai_messages"] >= 1
        assert removed["reminders"] == 1
        assert removed["activity_totals"] == 1
        after = personal_data.export(ME, database_url=seeded)
        assert after["bot_preferences"] is None
        assert after["playlists"] == []
        assert after["ai_messages"] == []

    def test_it_leaves_everybody_else_alone(self, seeded):
        personal_data.delete(ME, database_url=seeded)
        theirs = personal_data.export(SOMEBODY_ELSE, database_url=seeded)

        assert theirs["bot_preferences"]["default_city"] == "Cebu"
        assert [item["name"] for item in theirs["playlists"]] == ["Theirs"]
        assert any("their question" in message["content"] for message in theirs["ai_messages"])

    def test_somebody_elses_reminder_survives(self, seeded):
        personal_data.delete(ME, database_url=seeded)

        assert [row["message"] for row in reminders_repo.list_pending(SOMEBODY_ELSE, database_url=seeded)] == [
            "their reminder"
        ]

    def test_playlist_tracks_go_too(self, seeded):
        """Deleted explicitly, not by cascade: build_engine sets no
        PRAGMA foreign_keys=ON, so every ondelete="CASCADE" is decorative on
        SQLite -- which is the development and test database. Trusting the
        cascade would orphan every track here while working on Postgres."""
        personal_data.delete(ME, database_url=seeded)

        engine = get_engine(seeded)
        with engine.connect() as connection:
            titles = connection.execute(select(PlaylistTrack.title)).scalars().all()
        # Only the other person's track survives.
        assert titles == ["B"]

    def test_a_moderation_record_survives_a_deletion(self, seeded):
        """A member who could erase their own warning history by running one
        command has been handed a way to launder it."""
        personal_data.delete(ME, database_url=seeded)

        assert mod_repo.count_for_target("1", ME, database_url=seeded) == 1

    def test_the_audit_log_is_anonymised_rather_than_deleted(self, seeded):
        """A security-relevant record of who changed a server's settings, which
        the server owner has a legitimate interest in keeping. Removing the
        link to the person is the part that matters."""
        personal_data.delete(ME, database_url=seeded)

        engine = get_engine(seeded)
        with engine.connect() as connection:
            rows = connection.execute(select(AuditLog.actor_id, AuditLog.action)).mappings().all()

        actions = {row["action"] for row in rows}
        assert actions == {"settings.update", "player.skip"}
        assert personal_data.ANONYMISED_ACTOR in {row["actor_id"] for row in rows}
        assert ME not in {row["actor_id"] for row in rows}

    def test_a_shared_conversation_survives(self, seeded):
        """A conversation is keyed on the channel and holds several people's
        messages, so deleting the row to erase one person's lines would destroy
        everybody else's."""
        personal_data.delete(ME, database_url=seeded)
        conversation = ai_db.load_conversation("10", database_url=seeded)
        assert conversation is not None

        engine = get_engine(seeded)
        with engine.connect() as connection:
            authors = connection.execute(select(AIMessage.author_id)).scalars().all()
        assert ME not in authors
        assert SOMEBODY_ELSE in authors

    def test_deleting_twice_is_not_an_error(self, seeded):
        personal_data.delete(ME, database_url=seeded)
        again = personal_data.delete(ME, database_url=seeded)
        assert all(count == 0 for count in again.values())

    def test_it_reports_counts(self, seeded):
        """The person asking is entitled to see that something happened."""
        removed = personal_data.delete(ME, database_url=seeded)
        assert set(removed) >= {
            "playlists", "playlist_tracks", "ai_messages",
            "audit_entries_anonymised", "bot_preferences", "dashboard_account",
        }


class TestTheRetentionTable:
    def test_it_names_every_store_the_export_returns(self):
        """/privacy renders this table, so a store the code touches and the
        table omits would make the published policy inaccurate."""
        assert set(personal_data.RETENTION) >= {
            "Dashboard sign-ins", "Weather defaults", "Saved playlists",
            "AI conversations", "Server audit log", "AI usage counters", "Sessions",
            "Reminders",
            "Moderation record",
            "Activity counts",
        }

    def test_the_session_caveat_is_honest_about_the_limitation(self):
        caveat = personal_data.SESSION_CAVEAT
        assert "cannot be listed or revoked individually" in caveat
        # And about what it does *not* hold, which is the reassuring half.
        assert "no Discord access or refresh tokens" in caveat


class TestForgettingTheInMemoryBuffer:
    def test_it_drops_a_dm_buffer(self):
        from zephyr.services import gemini

        gemini.conversation_history[f"DM-{ME}"] = [{"role": "user", "text": "hi"}]
        assert gemini.forget_user_buffers(ME) >= 1
        assert f"DM-{ME}" not in gemini.conversation_history

    def test_it_leaves_a_guild_buffer_alone(self):
        """get_context_key keys a guild buffer on the *server*, so it holds
        several people's turns -- dropping it would erase everybody's context
        to satisfy one request. Their stored rows go regardless."""
        from zephyr.services import gemini

        gemini.conversation_history["SERVER-1"] = [{"role": "user", "text": "shared"}]
        try:
            gemini.forget_user_buffers(ME)
            assert "SERVER-1" in gemini.conversation_history
        finally:
            gemini.conversation_history.pop("SERVER-1", None)
