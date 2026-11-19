"""Subscription storage, and the scheduling arithmetic that decides what fires.

The DST cases are the reason this module exists: a digest is a wall-clock intent
in somebody else's timezone, and consecutive local days there are not always 24
hours apart.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from zephyr.db import weather_subs as repo


def _row(**overrides):
    base = dict(
        id=1, schedule_local_time="08:00", tz="Asia/Manila", last_run_at=None, kind="daily"
    )
    return SimpleNamespace(**{**base, **overrides})


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _sub(db_url, **overrides):
    values = dict(
        guild_id="1", channel_id="5", kind="daily", location="Iloilo City",
        lat=10.72, lon=122.56, units="metric", schedule_local_time="08:00",
        tz="Asia/Manila", thresholds=None, enabled=True,
    )
    values.update(overrides)
    return repo.create(values, database_url=db_url)


class TestIsDue:
    def test_not_due_before_the_local_time(self):
        # 23:00 UTC is 07:00 the next day in Manila (UTC+8) -- an hour early.
        assert repo.is_due(_row(), _utc(2026, 7, 30, 23)) is False

    def test_due_once_the_local_time_has_passed(self):
        assert repo.is_due(_row(), _utc(2026, 7, 31, 1)) is True

    def test_not_due_twice_in_the_same_local_day(self):
        row = _row(last_run_at=_utc(2026, 7, 31, 0, 5))
        assert repo.is_due(row, _utc(2026, 7, 31, 3)) is False

    def test_due_again_the_next_local_day(self):
        row = _row(last_run_at=_utc(2026, 7, 31, 0, 5))
        assert repo.is_due(row, _utc(2026, 8, 1, 1)) is True

    def test_the_local_day_is_the_subscribers_not_the_servers(self):
        """13:00 UTC is already the next day in Auckland, so a run recorded at
        that instant must count as *today* there, not yesterday."""
        row = _row(tz="Pacific/Auckland", schedule_local_time="08:00", last_run_at=_utc(2026, 7, 30, 20))
        # 2026-07-30T20:00Z is 2026-07-31T08:00 in Auckland (UTC+12).
        assert repo.is_due(row, _utc(2026, 7, 30, 21)) is False

    def test_a_dst_shift_does_not_skip_or_double_a_day(self):
        """New York springs forward on 2026-03-08. The day before and the day
        after are 23 hours apart, so an elapsed-hours rule would misfire; a
        local-date rule does not."""
        tz = "America/New_York"
        row = _row(tz=tz, schedule_local_time="08:00", last_run_at=_utc(2026, 3, 7, 13))
        # 2026-03-07T13:00Z is 08:00 in New York (EST, UTC-5): already ran that day.
        assert repo.is_due(row, _utc(2026, 3, 7, 15)) is False
        # 2026-03-08T12:00Z is 08:00 EDT (UTC-4) -- a new local day, 23h later.
        assert repo.is_due(row, _utc(2026, 3, 8, 12)) is True

    def test_a_naive_last_run_is_read_as_utc(self):
        """SQLite returns naive datetimes even from a timezone-aware column, and
        assuming the server's zone there turns a UTC stamp into a local one."""
        row = _row(last_run_at=datetime(2026, 7, 31, 0, 5))
        assert repo.is_due(row, _utc(2026, 7, 31, 3)) is False

    def test_a_row_with_no_schedule_never_fires_this_way(self):
        assert repo.is_due(_row(schedule_local_time=None), _utc(2026, 7, 31, 5)) is False

    def test_an_unparseable_schedule_is_skipped_not_raised(self):
        assert repo.is_due(_row(schedule_local_time="breakfast"), _utc(2026, 7, 31, 5)) is False

    def test_an_unknown_timezone_falls_back_to_utc_rather_than_never_firing(self):
        assert repo.is_due(_row(tz="Middle/Earth"), _utc(2026, 7, 31, 9)) is True


class TestRepository:
    def test_create_and_list(self, db_url):
        created = _sub(db_url)
        assert created["kind"] == "daily"
        assert [row["id"] for row in repo.list_for_guild("1", database_url=db_url)] == [created["id"]]

    def test_a_guild_is_capped(self, db_url):
        for index in range(repo.MAX_SUBS_PER_GUILD):
            _sub(db_url, channel_id=str(index))
        with pytest.raises(repo.SubscriptionError):
            _sub(db_url)

    def test_claiming_is_idempotent_within_a_local_day(self, db_url):
        _sub(db_url)
        now = _utc(2026, 7, 31, 1)  # 09:00 in Manila

        first = repo.claim_due(now, database_url=db_url)
        second = repo.claim_due(now + timedelta(minutes=1), database_url=db_url)

        assert len(first) == 1
        assert second == []

    def test_a_disabled_subscription_is_never_claimed(self, db_url):
        _sub(db_url, enabled=False)
        assert repo.claim_due(_utc(2026, 7, 31, 1), database_url=db_url) == []

    def test_watched_kinds_are_listed_not_claimed(self, db_url):
        """Severe alerts dedupe by fingerprint, so a quiet tick must not touch
        the row -- claiming it would suppress the next real warning."""
        _sub(db_url, kind="severe", schedule_local_time=None)

        watched = repo.list_watched(database_url=db_url)

        assert len(watched) == 1
        assert watched[0]["last_run_at"] is None
        assert repo.claim_due(_utc(2026, 7, 31, 1), database_url=db_url) == []

    def test_mark_fired_records_the_fingerprint(self, db_url):
        sub = _sub(db_url, kind="severe", schedule_local_time=None)
        repo.mark_fired(sub["id"], fingerprint="abc123", database_url=db_url)
        assert repo.get(sub["id"], database_url=db_url)["last_fingerprint"] == "abc123"

    def test_update_and_delete(self, db_url):
        sub = _sub(db_url)
        updated = repo.update_sub(sub["id"], {"enabled": False}, database_url=db_url)
        assert updated["enabled"] is False
        assert repo.delete_sub(sub["id"], database_url=db_url) is True
        assert repo.get(sub["id"], database_url=db_url) is None


class TestBotUsers:
    def test_a_default_location_round_trips(self, db_url):
        repo.write_bot_user("42", {"default_city": "Iloilo City", "lat": 10.72, "lon": 122.56}, database_url=db_url)
        stored = repo.read_bot_user("42", database_url=db_url)
        assert stored["default_city"] == "Iloilo City"
        assert stored["units"] == "metric"

    def test_a_partial_write_does_not_blank_the_rest(self, db_url):
        repo.write_bot_user("42", {"default_city": "Iloilo City", "lat": 1.0, "lon": 2.0}, database_url=db_url)
        repo.write_bot_user("42", {"units": "imperial"}, database_url=db_url)

        stored = repo.read_bot_user("42", database_url=db_url)
        assert stored["default_city"] == "Iloilo City"
        assert stored["units"] == "imperial"

    def test_clearing_removes_the_row(self, db_url):
        repo.write_bot_user("42", {"default_city": "Iloilo City"}, database_url=db_url)
        assert repo.clear_bot_user("42", database_url=db_url) is True
        assert repo.read_bot_user("42", database_url=db_url) is None


class TestParseLocalTime:
    @pytest.mark.parametrize("value", [None, "", "8am", "25:00", "08:70", "08"])
    def test_bad_schedules_are_refused_with_a_message(self, value):
        with pytest.raises(repo.SubscriptionError):
            repo.parse_local_time(value)

    def test_a_valid_schedule_parses(self):
        assert repo.parse_local_time("08:05").hour == 8


def test_a_digest_created_after_todays_time_waits_for_tomorrow(db_url):
    from datetime import datetime as real_datetime

    now = real_datetime.now(timezone.utc)
    passed = (now - timedelta(minutes=5)).strftime("%H:%M")
    sub = _sub(db_url, schedule_local_time=passed, tz="UTC")
    if now.strftime("%H:%M") < passed:   # crossed midnight while computing
        return
    assert sub["last_run_at"] is not None
    assert repo.claim_due(now, database_url=db_url) == []


def test_release_claim_makes_a_digest_due_again(db_url):
    _sub(db_url)
    now = _utc(2026, 7, 31, 1)
    claimed = repo.claim_due(now, database_url=db_url)
    repo.release_claim(claimed[0]["id"], claimed[0]["last_run_at"], database_url=db_url)
    assert len(repo.claim_due(now + timedelta(minutes=1), database_url=db_url)) == 1


def test_an_identical_subscription_is_refused(db_url):
    _sub(db_url)
    with pytest.raises(repo.SubscriptionError):
        _sub(db_url)
    # A different time or channel is a different subscription.
    _sub(db_url, schedule_local_time="18:00")
    _sub(db_url, channel_id="6")


def test_re_enabling_a_digest_after_its_time_waits_for_tomorrow(db_url):
    """The create-time rule now applies to edits: a paused 08:00 digest
    re-enabled at 15:00 used to post within a minute."""
    created = _sub(db_url, enabled=False, schedule_local_time="00:00", tz="UTC")
    repo.update_sub(created["id"], {"enabled": True}, database_url=db_url)

    assert repo.claim_due(datetime.now(timezone.utc), database_url=db_url) == []


def test_an_unrelated_edit_does_not_touch_the_schedule(db_url):
    created = _sub(db_url, schedule_local_time="23:59", tz="UTC")
    updated = repo.update_sub(created["id"], {"channel_id": "9"}, database_url=db_url)
    assert updated["last_run_at"] == created["last_run_at"]


def test_clear_fingerprint(db_url):
    created = _sub(db_url, kind="severe", schedule_local_time=None)
    repo.mark_fired(created["id"], fingerprint="abc", database_url=db_url)
    repo.clear_fingerprint(created["id"], database_url=db_url)
    assert repo.get(created["id"], database_url=db_url)["last_fingerprint"] is None


def test_a_zone_folder_is_refused_not_an_error():
    from datetime import timezone

    from zephyr.db.weather_subs import normalise_zone, zone

    assert normalise_zone("America") == ("UTC", False)
    assert zone("Etc") is timezone.utc


# --- Fourth pass -----------------------------------------------------------------

def _freeze(monkeypatch, moment):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is None else moment.astimezone(tz)

    monkeypatch.setattr(repo, "datetime", Frozen)


class TestSkippedDays:
    def test_a_skipped_digest_moved_later_still_posts_today(self, db_url, monkeypatch):
        """Created 15:00 for 08:00, then moved to 20:00: it used to stay silent."""
        _freeze(monkeypatch, _utc(2026, 7, 31, 7))            # 15:00 Manila
        created = _sub(db_url)
        assert repo.is_skip_marker(created["last_run_at"], "Asia/Manila")

        updated = repo.update_sub(created["id"], {"schedule_local_time": "20:00"}, database_url=db_url)
        row = SimpleNamespace(**updated)
        assert repo.is_due(row, _utc(2026, 7, 31, 12, 1)) is True  # 20:01 Manila

    def test_a_digest_that_really_posted_does_not_post_again_when_moved(self, db_url, monkeypatch):
        _freeze(monkeypatch, _utc(2026, 7, 30, 23))           # 07:00 Manila
        created = _sub(db_url)
        repo.mark_fired(created["id"], at=_utc(2026, 7, 31, 0, 1), database_url=db_url)  # posted 08:01
        _freeze(monkeypatch, _utc(2026, 7, 31, 7))

        updated = repo.update_sub(created["id"], {"schedule_local_time": "20:00"}, database_url=db_url)
        assert repo.is_due(SimpleNamespace(**updated), _utc(2026, 7, 31, 12, 1)) is False

    def test_a_skip_is_still_today_so_it_waits_for_tomorrow(self, monkeypatch):
        marker = repo.skip_marker("Asia/Manila", _utc(2026, 7, 31, 7))
        assert repo.is_due(_row(last_run_at=marker), _utc(2026, 7, 31, 8)) is False
        assert repo.is_due(_row(last_run_at=marker), _utc(2026, 8, 1, 1)) is True
        assert not repo.is_skip_marker(_utc(2026, 7, 31, 0, 1), "Asia/Manila")


class TestDuplicateEdits:
    def test_an_edit_cannot_copy_another_subscription(self, db_url):
        _sub(db_url, schedule_local_time="08:00")
        other = _sub(db_url, schedule_local_time="09:00")
        with pytest.raises(repo.SubscriptionError, match="same subscription"):
            repo.update_sub(other["id"], {"schedule_local_time": "08:00"}, database_url=db_url)

    def test_an_edit_that_leaves_it_unique_is_fine(self, db_url):
        created = _sub(db_url)
        updated = repo.update_sub(created["id"], {"schedule_local_time": "08:00"}, database_url=db_url)
        assert updated["schedule_local_time"] == "08:00"

    def test_a_time_on_a_watch_does_not_dodge_the_duplicate_check(self, db_url):
        first = _sub(db_url, kind="severe", schedule_local_time=None)
        assert first["schedule_local_time"] is None
        with pytest.raises(repo.SubscriptionError, match="same subscription"):
            _sub(db_url, kind="severe", schedule_local_time="08:00")
