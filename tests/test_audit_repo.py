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
