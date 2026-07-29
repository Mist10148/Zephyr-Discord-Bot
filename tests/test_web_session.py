"""Tests for the Redis-backed session store.

Redis is the in-memory double from conftest, so there are no network calls.
"""

import json
import time

import pytest

from website.session import (
    SESSION_PREFIX,
    STATE_PREFIX,
    SessionStoreError,
    consume_state,
    create_session,
    destroy,
    load_session,
    save_session,
    store_state,
)

TTL = 3600
MAX_AGE = 30 * 24 * 3600


def _make_user(user_id="900000000000000001"):
    return {"id": user_id, "username": "tester", "global_name": "Tester", "avatar": "avhash"}


def _make_guilds():
    return [{"id": "1", "name": "One", "icon": None, "owner": True}]


class TestCreateSession:
    def test_ids_are_unique(self, fake_redis):
        ids = {create_session(_make_user(), [], ttl=TTL).sid for _ in range(100)}
        assert len(ids) == 100

    def test_round_trip_preserves_every_field(self, fake_redis):
        created = create_session(_make_user(), _make_guilds(), ttl=TTL)
        loaded = load_session(created.sid, ttl=TTL, max_age=MAX_AGE)
        assert loaded is not None
        assert loaded.user_id == "900000000000000001"
        assert loaded.username == "tester"
        assert loaded.global_name == "Tester"
        assert loaded.avatar_hash == "avhash"
        assert loaded.csrf == created.csrf
        assert loaded.created_at == created.created_at
        assert loaded.guilds == _make_guilds()
        assert loaded.guilds_fetched_at == created.guilds_fetched_at

    def test_stores_under_the_namespaced_key_with_a_ttl(self, fake_redis):
        session = create_session(_make_user(), [], ttl=TTL)
        remaining = fake_redis.ttl_of(SESSION_PREFIX + session.sid)
        assert remaining is not None
        assert 0 < remaining <= TTL

    def test_csrf_token_differs_from_the_session_id(self, fake_redis):
        session = create_session(_make_user(), [], ttl=TTL)
        assert session.csrf != session.sid
        assert len(session.csrf) >= 32

    def test_manageable_ids(self, fake_redis):
        session = create_session(_make_user(), [{"id": 7, "name": "Seven"}], ttl=TTL)
        assert session.manageable_ids() == {"7"}

    def test_a_write_failure_raises(self, fake_redis):
        fake_redis.raise_on = ConnectionError("redis is down")
        with pytest.raises(SessionStoreError):
            create_session(_make_user(), [], ttl=TTL)

    def test_a_missing_redis_url_raises(self, monkeypatch):
        """No dev fallback: an in-memory store would work on one worker only."""
        monkeypatch.setattr("zephyr.services.redis_client.REDIS_URL", None, raising=False)
        from zephyr.services import redis_client

        redis_client.close_clients()
        with pytest.raises(SessionStoreError):
            create_session(_make_user(), [], ttl=TTL, redis_url=None)


class TestLoadSession:
    def test_unknown_id_is_none(self, fake_redis):
        assert load_session("nope", ttl=TTL, max_age=MAX_AGE) is None

    def test_no_id_is_none(self, fake_redis):
        assert load_session(None, ttl=TTL, max_age=MAX_AGE) is None

    def test_ttl_slides_on_every_load(self, fake_redis):
        session = create_session(_make_user(), [], ttl=10)
        key = SESSION_PREFIX + session.sid
        assert fake_redis.ttl_of(key) <= 10
        load_session(session.sid, ttl=TTL, max_age=MAX_AGE)
        renewed = fake_redis.ttl_of(key)
        assert renewed > 10
        assert renewed <= TTL

    def test_an_expired_key_is_gone(self, fake_redis):
        session = create_session(_make_user(), [], ttl=TTL)
        fake_redis.expire_now(SESSION_PREFIX + session.sid)
        assert load_session(session.sid, ttl=TTL, max_age=MAX_AGE) is None

    def test_the_absolute_cap_beats_activity(self, fake_redis):
        """A session kept alive by use still dies at max_age."""
        session = create_session(_make_user(), [], ttl=TTL)
        key = SESSION_PREFIX + session.sid
        stored = json.loads(fake_redis.get(key))
        stored["created_at"] = int(time.time()) - (MAX_AGE + 60)
        fake_redis.set(key, json.dumps(stored), ex=TTL)
        assert load_session(session.sid, ttl=TTL, max_age=MAX_AGE) is None
        assert fake_redis.get(key) is None

    def test_malformed_json_is_deleted_and_treated_as_no_session(self, fake_redis):
        fake_redis.set(SESSION_PREFIX + "broken", "{not json", ex=TTL)
        assert load_session("broken", ttl=TTL, max_age=MAX_AGE) is None
        assert fake_redis.get(SESSION_PREFIX + "broken") is None

    def test_a_missing_required_field_is_treated_as_no_session(self, fake_redis):
        fake_redis.set(SESSION_PREFIX + "partial", json.dumps({"username": "x"}), ex=TTL)
        assert load_session("partial", ttl=TTL, max_age=MAX_AGE) is None

    def test_a_redis_outage_raises_instead_of_looking_like_a_logout(self, fake_redis):
        """The deliberate divergence from RedisStorage's soft-fail."""
        session = create_session(_make_user(), [], ttl=TTL)
        fake_redis.raise_on = ConnectionError("redis is down")
        with pytest.raises(SessionStoreError):
            load_session(session.sid, ttl=TTL, max_age=MAX_AGE)


class TestSaveAndDestroy:
    def test_save_preserves_the_id_and_persists_changes(self, fake_redis):
        session = create_session(_make_user(), [], ttl=TTL)
        session.guilds = _make_guilds()
        save_session(session, ttl=TTL)
        loaded = load_session(session.sid, ttl=TTL, max_age=MAX_AGE)
        assert loaded.sid == session.sid
        assert loaded.guilds == _make_guilds()

    def test_destroy_removes_the_key(self, fake_redis):
        session = create_session(_make_user(), [], ttl=TTL)
        destroy(session.sid)
        assert fake_redis.get(SESSION_PREFIX + session.sid) is None
        assert load_session(session.sid, ttl=TTL, max_age=MAX_AGE) is None

    def test_destroying_nothing_is_not_an_error(self, fake_redis):
        destroy(None)
        destroy("never-existed")


class TestOAuthState:
    def test_round_trip(self, fake_redis):
        store_state("abc", {"next": "/g/1"}, ttl=600)
        assert consume_state("abc") == {"next": "/g/1"}

    def test_stored_with_the_configured_ttl(self, fake_redis):
        store_state("abc", {}, ttl=600)
        remaining = fake_redis.ttl_of(STATE_PREFIX + "abc")
        assert 0 < remaining <= 600

    def test_single_use(self, fake_redis):
        """GETDEL is what makes replaying a callback URL fail."""
        store_state("abc", {"next": None}, ttl=600)
        assert consume_state("abc") is not None
        assert consume_state("abc") is None

    def test_unknown_state_is_none(self, fake_redis):
        assert consume_state("missing") is None

    def test_malformed_state_is_none(self, fake_redis):
        fake_redis.set(STATE_PREFIX + "abc", "{not json", ex=600)
        assert consume_state("abc") is None

    def test_non_mapping_state_is_none(self, fake_redis):
        fake_redis.set(STATE_PREFIX + "abc", json.dumps(["list"]), ex=600)
        assert consume_state("abc") is None

    def test_outages_raise(self, fake_redis):
        fake_redis.raise_on = ConnectionError("redis is down")
        with pytest.raises(SessionStoreError):
            store_state("abc", {}, ttl=600)
        with pytest.raises(SessionStoreError):
            consume_state("abc")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
