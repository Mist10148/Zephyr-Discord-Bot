"""Tests for the Phase 7 audit reader, ``zephyr.db.audit.read``.

Runs against a throwaway SQLite file, like the rest of the db-layer tests. The
writer (``record``) has been exercised indirectly since Phase 4; this pins down
the reader's ordering, paging and guild scoping.
"""

import pytest

from zephyr.db import audit
from zephyr.db.engine import build_engine, create_schema
from zephyr.db.session import get_engine


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{(tmp_path / 'audit.db').as_posix()}"
    # record()/read() reach the database through get_engine(url); create the schema
    # on that same engine so both see one database.
    create_schema(get_engine(url))
    return url


def _record(db, guild_id, action, **payload):
    audit.record(action, actor_id="900", guild_id=guild_id, payload=payload or None, source="web", database_url=db)


class TestOrdering:
    def test_newest_first(self, db):
        for action in ("first", "second", "third"):
            _record(db, "1", action)
        page = audit.read("1", database_url=db)
        assert [e["action"] for e in page["entries"]] == ["third", "second", "first"]

    def test_entry_shape_is_serialisable(self, db):
        _record(db, "1", "settings.update", prefix="!")
        entry = audit.read("1", database_url=db)["entries"][0]
        assert set(entry) == {"id", "guild_id", "actor_id", "action", "payload", "source", "created_at"}
        assert entry["payload"] == {"prefix": "!"}
        assert isinstance(entry["created_at"], str)  # ISO string, not a datetime


class TestScoping:
    def test_only_the_asked_for_guild(self, db):
        _record(db, "1", "mine")
        _record(db, "2", "theirs")
        actions = [e["action"] for e in audit.read("1", database_url=db)["entries"]]
        assert actions == ["mine"]

    def test_an_empty_guild_is_an_empty_page_not_an_error(self, db):
        page = audit.read("404", database_url=db)
        assert page == {"entries": [], "next_before": None}


class TestPaging:
    def test_limit_caps_the_page_and_reports_a_cursor(self, db):
        for n in range(5):
            _record(db, "1", f"a{n}")
        page = audit.read("1", limit=2, database_url=db)
        assert len(page["entries"]) == 2
        assert page["next_before"] == page["entries"][-1]["id"]

    def test_before_walks_backwards_without_gaps_or_repeats(self, db):
        for n in range(5):
            _record(db, "1", f"a{n}")
        seen = []
        cursor = None
        for _ in range(10):  # generous bound; the loop breaks itself
            page = audit.read("1", limit=2, before_id=cursor, database_url=db)
            seen += [e["id"] for e in page["entries"]]
            cursor = page["next_before"]
            if cursor is None:
                break
        assert seen == sorted(seen, reverse=True)
        assert len(seen) == len(set(seen)) == 5

    def test_last_page_has_no_cursor(self, db):
        for n in range(3):
            _record(db, "1", f"a{n}")
        assert audit.read("1", limit=3, database_url=db)["next_before"] is None

    def test_limit_is_clamped_to_the_ceiling(self, db):
        page = audit.read("1", limit=10_000, database_url=db)
        assert page == {"entries": [], "next_before": None}  # clamped, and simply empty


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestFilters:
    """F4: filtering server-side, because the page is keyset-paginated.

    A client-side filter can only narrow the fifty rows it already holds, so
    "every volume change this month" means paging the whole log by hand.
    """

    def _seed(self, db):
        for action, actor, source in (
            ("player.volume", "1", "web"),
            ("player.skip", "1", "web"),
            ("settings.update", "2", "web"),
            ("ai.memory.purge", "2", "discord"),
        ):
            audit.record(guild_id="1", actor_id=actor, action=action, source=source, database_url=db)

    def test_action_matches_a_family_not_one_verb(self, db):
        self._seed(db)
        page = audit.read("1", action="player", database_url=db)
        # Prefix, not equality: a filter usable only by someone who already
        # knows the verb names is not a filter.
        assert {entry["action"] for entry in page["entries"]} == {"player.volume", "player.skip"}

    def test_a_full_action_still_matches_exactly_one(self, db):
        self._seed(db)
        page = audit.read("1", action="player.skip", database_url=db)
        assert [entry["action"] for entry in page["entries"]] == ["player.skip"]

    def test_actor_and_source_are_equality(self, db):
        self._seed(db)
        assert len(audit.read("1", actor_id="2", database_url=db)["entries"]) == 2
        assert len(audit.read("1", source="discord", database_url=db)["entries"]) == 1

    def test_filters_combine(self, db):
        self._seed(db)
        page = audit.read("1", actor_id="2", source="web", database_url=db)
        assert [entry["action"] for entry in page["entries"]] == ["settings.update"]

    def test_a_filter_matching_nothing_is_an_empty_page(self, db):
        self._seed(db)
        assert audit.read("1", action="nope", database_url=db) == {"entries": [], "next_before": None}

    def test_filtering_still_pages(self, db):
        for index in range(5):
            audit.record(guild_id="1", actor_id="1", action=f"player.v{index}", database_url=db)
        audit.record(guild_id="1", actor_id="1", action="settings.update", database_url=db)

        first = audit.read("1", action="player", limit=2, database_url=db)
        assert len(first["entries"]) == 2
        assert first["next_before"] is not None
        # The cursor must stay inside the filtered set, not skip to an
        # unfiltered row.
        second = audit.read("1", action="player", limit=2, before_id=first["next_before"], database_url=db)
        assert all(entry["action"].startswith("player") for entry in second["entries"])
        assert not {entry["id"] for entry in first["entries"]} & {entry["id"] for entry in second["entries"]}
