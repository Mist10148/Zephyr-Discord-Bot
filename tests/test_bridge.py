"""Tests for the bot -> web guild snapshot.

Redis is the in-memory double from conftest, so no network calls happen.
"""

import json

import pytest

from zephyr.services.bridge import GUILDS_KEY, GUILDS_UPDATED_KEY, read_guild_snapshot, write_guild_snapshot


def _make_guild(guild_id="1", name="Test Guild", icon="abc123"):
    return {"id": guild_id, "name": name, "icon": icon}


class TestWriteGuildSnapshot:
    def test_writes_a_map_keyed_by_id(self, fake_redis):
        write_guild_snapshot([_make_guild("1"), _make_guild("2", "Other", None)])
        stored = json.loads(fake_redis.get(GUILDS_KEY))
        assert set(stored) == {"1", "2"}
        assert stored["2"]["name"] == "Other"
        assert stored["2"]["icon"] is None

    def test_coerces_integer_ids_to_strings(self, fake_redis):
        write_guild_snapshot([{"id": 123456789012345678, "name": "Big", "icon": None}])
        assert "123456789012345678" in json.loads(fake_redis.get(GUILDS_KEY))

    def test_stamps_the_update_time(self, fake_redis):
        write_guild_snapshot([_make_guild()])
        assert int(fake_redis.get(GUILDS_UPDATED_KEY)) > 0

    def test_the_snapshot_never_expires(self, fake_redis):
        """Expiring membership while the bot is down would empty the picker."""
        write_guild_snapshot([_make_guild()])
        assert fake_redis.ttl_of(GUILDS_KEY) is None
        assert fake_redis.ttl_of(GUILDS_UPDATED_KEY) is None

    def test_a_later_write_replaces_the_previous_snapshot(self, fake_redis):
        write_guild_snapshot([_make_guild("1"), _make_guild("2")])
        write_guild_snapshot([_make_guild("2")])
        assert set(json.loads(fake_redis.get(GUILDS_KEY))) == {"2"}


class TestReadGuildSnapshot:
    def test_round_trip(self, fake_redis):
        write_guild_snapshot([_make_guild("42", "Answer")])
        guilds, updated = read_guild_snapshot()
        assert guilds["42"]["name"] == "Answer"
        assert isinstance(updated, int)

    def test_unpublished_is_none_not_empty(self, fake_redis):
        """None means "the bot never published", which is not "in no guilds"."""
        guilds, updated = read_guild_snapshot()
        assert guilds is None
        assert updated is None

    def test_malformed_json_is_treated_as_unpublished(self, fake_redis):
        fake_redis.set(GUILDS_KEY, "{not json")
        assert read_guild_snapshot() == (None, None)

    def test_non_mapping_payload_is_treated_as_unpublished(self, fake_redis):
        fake_redis.set(GUILDS_KEY, json.dumps([1, 2, 3]))
        assert read_guild_snapshot() == (None, None)

    def test_a_missing_timestamp_still_returns_the_guilds(self, fake_redis):
        fake_redis.set(GUILDS_KEY, json.dumps({"1": _make_guild()}))
        guilds, updated = read_guild_snapshot()
        assert set(guilds) == {"1"}
        assert updated is None

    def test_an_unreadable_timestamp_does_not_break_the_read(self, fake_redis):
        write_guild_snapshot([_make_guild()])
        fake_redis.set(GUILDS_UPDATED_KEY, "not-a-number")
        guilds, updated = read_guild_snapshot()
        assert guilds is not None
        assert updated is None

    def test_redis_errors_propagate(self, fake_redis):
        """The web tier must be able to tell "no snapshot" from "Redis is down"."""
        fake_redis.raise_on = ConnectionError("redis is down")
        with pytest.raises(ConnectionError):
            read_guild_snapshot()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
