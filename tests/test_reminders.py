"""Reminders: the parser, the claim, and the repeat arithmetic.

The claim and the repeat are the two places this can go wrong in a way nobody
notices for a week -- a reminder delivered twice, or a daily reminder that
drifts an hour later every day -- so both are pinned here rather than left to a
manual smoke test.

Async methods are driven with asyncio.run, matching test_weather_scheduler.py,
so no pytest-asyncio dependency is needed.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import discord
import pytest

from zephyr.cogs import reminders as cog_module
from zephyr.cogs.reminders import RemindersCog, format_reminder, parse_delay
from zephyr.db import reminders as repo


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _values(**over):
    base = {
        "user_id": "1",
        "guild_id": "9",
        "channel_id": "5",
        "message": "stand up",
        "due_at": _utc(2026, 9, 5, 12),
        "tz": "UTC",
        "repeat_every_seconds": None,
        "attempts": 0,
        "source": "discord",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


class TestParseDelay:
    @pytest.mark.parametrize(
        "text,seconds",
        [
            ("30s", 30),
            ("20m", 1200),
            ("2h", 7200),
            ("3 days", 259200),
            ("1w", 604800),
            ("1h30m", 5400),
            ("  2 HOURS  ", 7200),
            ("1 day 12 hours", 129600),
        ],
    )
    def test_it_reads_the_forms_people_actually_type(self, text, seconds):
        assert parse_delay(text) == seconds

    @pytest.mark.parametrize("text", ["", "soon", "tomorrow", "5", "5 fortnights", "h"])
    def test_it_refuses_what_it_cannot_read(self, text):
        assert parse_delay(text) is None

    def test_trailing_nonsense_is_refused_not_partially_read(self):
        """"1h and also nonsense" must not silently become an hour.

        Reading the leading duration and discarding the rest is the dangerous
        failure: the person believes they said something the bot did not hear.
        """
        assert parse_delay("1h and also nonsense") is None

    def test_a_bare_and_between_units_is_still_read(self):
        assert parse_delay("1h and 30m") == 5400

    def test_zero_is_not_a_delay(self):
        assert parse_delay("0m") is None


class TestFormatReminder:
    def test_it_renders_a_discord_timestamp_so_every_reader_sees_their_own_zone(self):
        due = _utc(2026, 9, 5, 12)
        text = format_reminder({"due_at": due, "repeat_every_seconds": None})
        stamp = int(due.timestamp())
        assert f"<t:{stamp}:f>" in text
        assert f"<t:{stamp}:R>" in text

    def test_a_repeat_is_named_in_words(self):
        text = format_reminder({"due_at": _utc(2026, 9, 5), "repeat_every_seconds": 86400})
        assert "repeats every 1 day" in text

    def test_a_naive_due_at_is_read_as_utc(self):
        """SQLite hands back naive datetimes even from a timezone=True column.

        Without this, the local timezone would silently be applied and every
        stamp would be hours off on a non-UTC host.
        """
        naive = format_reminder({"due_at": datetime(2026, 9, 5, 12), "repeat_every_seconds": None})
        aware = format_reminder({"due_at": _utc(2026, 9, 5, 12), "repeat_every_seconds": None})
        assert naive == aware


# ---------------------------------------------------------------------------
# The repository
# ---------------------------------------------------------------------------


class TestCreate:
    def test_a_created_reminder_comes_back_whole(self, db_url):
        row = repo.create(_values(), database_url=db_url)
        assert row["message"] == "stand up"
        assert row["fired_at"] is None
        assert row["id"]

    def test_a_dm_reminder_has_no_guild(self, db_url):
        row = repo.create(_values(guild_id=None), database_url=db_url)
        assert row["guild_id"] is None

    def test_the_per_user_cap_is_enforced(self, db_url, monkeypatch):
        monkeypatch.setattr(repo, "MAX_PENDING_PER_USER", 2)
        repo.create(_values(), database_url=db_url)
        repo.create(_values(), database_url=db_url)

        with pytest.raises(repo.ReminderError):
            repo.create(_values(), database_url=db_url)

    def test_the_cap_counts_only_pending_reminders(self, db_url, monkeypatch):
        """A delivered reminder must not occupy a slot forever.

        Counting every row would mean a heavy user was permanently locked out
        by their own history.
        """
        monkeypatch.setattr(repo, "MAX_PENDING_PER_USER", 1)
        repo.create(_values(due_at=_utc(2026, 9, 5, 1)), database_url=db_url)
        repo.claim_due(_utc(2026, 9, 5, 2), database_url=db_url)

        assert repo.create(_values(), database_url=db_url)["id"]

    def test_the_cap_is_per_person(self, db_url, monkeypatch):
        monkeypatch.setattr(repo, "MAX_PENDING_PER_USER", 1)
        repo.create(_values(user_id="1"), database_url=db_url)
        assert repo.create(_values(user_id="2"), database_url=db_url)["id"]


class TestListPending:
    def test_it_lists_one_persons_reminders_soonest_first(self, db_url):
        late = repo.create(_values(due_at=_utc(2026, 9, 6)), database_url=db_url)
        early = repo.create(_values(due_at=_utc(2026, 9, 5)), database_url=db_url)
        repo.create(_values(user_id="2"), database_url=db_url)

        rows = repo.list_pending("1", database_url=db_url)

        assert [row["id"] for row in rows] == [early["id"], late["id"]]

    def test_a_delivered_reminder_is_not_pending(self, db_url):
        repo.create(_values(due_at=_utc(2026, 9, 5, 1)), database_url=db_url)
        repo.claim_due(_utc(2026, 9, 5, 2), database_url=db_url)

        assert repo.list_pending("1", database_url=db_url) == []


class TestClaimDue:
    def test_it_returns_only_what_is_due(self, db_url):
        due = repo.create(_values(due_at=_utc(2026, 9, 5, 11)), database_url=db_url)
        repo.create(_values(due_at=_utc(2026, 9, 5, 13)), database_url=db_url)

        claimed = repo.claim_due(_utc(2026, 9, 5, 12), database_url=db_url)

        assert [row["id"] for row in claimed] == [due["id"]]

    def test_a_claim_is_not_repeated(self, db_url):
        """The whole point of claiming inside the transaction.

        A second pass over the same instant -- another worker, or the next tick
        before delivery finished -- must find nothing, or the person gets the
        reminder twice.
        """
        repo.create(_values(due_at=_utc(2026, 9, 5, 11)), database_url=db_url)

        first = repo.claim_due(_utc(2026, 9, 5, 12), database_url=db_url)
        second = repo.claim_due(_utc(2026, 9, 5, 12), database_url=db_url)

        assert len(first) == 1
        assert second == []

    def test_claiming_records_the_attempt(self, db_url):
        created = repo.create(_values(due_at=_utc(2026, 9, 5, 11)), database_url=db_url)
        repo.claim_due(_utc(2026, 9, 5, 12), database_url=db_url)

        assert repo.get(created["id"], database_url=db_url)["attempts"] == 1

    def test_the_batch_is_bounded(self, db_url, monkeypatch):
        """A backlog must drain over several ticks, not block the loop.

        Nothing else limits how many rows come back, so an outage that left
        five thousand reminders due would otherwise be one enormous pass.
        """
        monkeypatch.setattr(repo, "CLAIM_BATCH", 2)
        for _ in range(4):
            repo.create(_values(due_at=_utc(2026, 9, 5, 11)), database_url=db_url)

        assert len(repo.claim_due(_utc(2026, 9, 5, 12), database_url=db_url)) == 2


class TestReschedule:
    def test_a_repeat_advances_from_the_previous_due_time_not_from_now(self, db_url):
        """Otherwise a daily reminder drifts by however long delivery took.

        Twenty seconds a day is four hours over a year, which is how "08:00
        every morning" becomes lunchtime.
        """
        created = repo.create(
            _values(due_at=_utc(2026, 9, 5, 8), repeat_every_seconds=86400),
            database_url=db_url,
        )
        repo.claim_due(_utc(2026, 9, 5, 8), database_url=db_url)

        # Delivered 20 seconds late.
        row = repo.reschedule(
            created["id"], from_time=_utc(2026, 9, 5, 8) + timedelta(seconds=20),
            database_url=db_url,
        )

        assert repo._as_utc(row["due_at"]) == _utc(2026, 9, 6, 8)

    def test_missed_occurrences_are_wound_past_not_fired_one_by_one(self, db_url):
        """A bot offline for three days must not fire a daily reminder thrice.

        Advancing by a single interval would leave the next due time still in
        the past, so the following tick would claim it again immediately.
        """
        created = repo.create(
            _values(due_at=_utc(2026, 9, 1, 8), repeat_every_seconds=86400),
            database_url=db_url,
        )

        row = repo.reschedule(created["id"], from_time=_utc(2026, 9, 4, 9), database_url=db_url)

        assert repo._as_utc(row["due_at"]) == _utc(2026, 9, 5, 8)

    def test_rescheduling_unclaims_the_row(self, db_url):
        created = repo.create(
            _values(due_at=_utc(2026, 9, 5, 8), repeat_every_seconds=86400),
            database_url=db_url,
        )
        repo.claim_due(_utc(2026, 9, 5, 8), database_url=db_url)
        repo.reschedule(created["id"], from_time=_utc(2026, 9, 5, 8), database_url=db_url)

        assert repo.get(created["id"], database_url=db_url)["fired_at"] is None

    def test_a_one_shot_is_not_rescheduled(self, db_url):
        created = repo.create(_values(), database_url=db_url)
        assert repo.reschedule(created["id"], from_time=_utc(2026, 9, 5), database_url=db_url) is None

    def test_a_missing_reminder_is_not_an_error(self, db_url):
        assert repo.reschedule(999, from_time=_utc(2026, 9, 5), database_url=db_url) is None


class TestCancel:
    def test_the_owner_can_cancel(self, db_url):
        created = repo.create(_values(user_id="1"), database_url=db_url)
        assert repo.cancel(created["id"], "1", database_url=db_url) is True
        assert repo.get(created["id"], database_url=db_url) is None

    def test_somebody_else_cannot(self, db_url):
        """Ids are sequential across the whole database.

        Without the owner clause in the statement, guessing "3" would cancel
        whoever happens to own reminder 3.
        """
        created = repo.create(_values(user_id="1"), database_url=db_url)

        assert repo.cancel(created["id"], "2", database_url=db_url) is False
        assert repo.get(created["id"], database_url=db_url) is not None


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


class FakeChannel:
    def __init__(self, raises=None):
        self.sent = []
        self._raises = raises

    async def send(self, *, content=None, embed=None):
        if self._raises:
            raise self._raises
        self.sent.append((content, embed))


class FakeBot:
    def __init__(self, channel=None, user=None):
        self._channel = channel
        self._user = user
        self.fetched = []

    def get_channel(self, _id):
        return self._channel

    def get_user(self, _id):
        return self._user

    async def fetch_user(self, user_id):
        self.fetched.append(user_id)
        return self._user


def _row(**over):
    base = {
        "id": 7, "user_id": "1", "guild_id": "9", "channel_id": "5",
        "message": "stand up", "repeat_every_seconds": None,
    }
    base.update(over)
    return base


def _deliver(cog, row):
    return asyncio.run(cog._deliver(row))


class TestDelivery:
    def test_a_due_reminder_is_posted_and_mentions_the_person(self, monkeypatch):
        channel = FakeChannel()
        assert _deliver(RemindersCog(FakeBot(channel)), _row()) is True

        content, embed = channel.sent[0]
        assert content == "<@1>"
        assert embed.description == "stand up"

    def test_a_dm_reminder_falls_back_to_the_user(self):
        """A DM channel is not in the cache after a restart.

        get_channel returns None for it, so without the user fallback every
        reminder set in a DM would silently never arrive.
        """
        user = FakeChannel()
        bot = FakeBot(channel=None, user=user)

        assert _deliver(RemindersCog(bot), _row(guild_id=None)) is True
        assert len(user.sent) == 1

    def test_an_unreachable_destination_is_reported_not_raised(self, caplog):
        bot = FakeBot(channel=None, user=None)
        with caplog.at_level("WARNING", logger="zephyr.cogs.reminders"):
            assert _deliver(RemindersCog(bot), _row(guild_id=None)) is False
        assert "no reachable destination" in caplog.text

    def test_a_forbidden_channel_does_not_raise(self, caplog):
        channel = FakeChannel(raises=discord.Forbidden(_Response(403), "nope"))
        with caplog.at_level("WARNING", logger="zephyr.cogs.reminders"):
            assert _deliver(RemindersCog(FakeBot(channel)), _row()) is False

    def test_one_bad_row_does_not_stop_the_rest_of_the_batch(self, monkeypatch, caplog):
        """The reason _deliver returns rather than raises.

        A single reminder whose channel was deleted must not prevent the
        ninety-nine behind it in the same batch from being delivered.
        """
        good = FakeChannel()
        cog = RemindersCog(FakeBot(good))
        rows = [_row(id=1, channel_id="bad-not-an-int"), _row(id=2)]

        with caplog.at_level("ERROR", logger="zephyr.cogs.reminders"):
            delivered = [asyncio.run(cog._deliver(row)) for row in rows]

        assert delivered == [False, True]
        assert len(good.sent) == 1

    def test_a_repeating_reminder_is_rescheduled_after_it_is_sent(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            cog_module.repo, "reschedule",
            lambda reminder_id, *, from_time: calls.append(reminder_id),
        )
        _deliver(RemindersCog(FakeBot(FakeChannel())), _row(repeat_every_seconds=86400))

        assert calls == [7]

    def test_a_failed_send_is_not_rescheduled(self, monkeypatch, caplog):
        """Rescheduling a reminder that never arrived would skip an occurrence
        silently -- the row moves forward as though it had been delivered."""
        calls = []
        monkeypatch.setattr(
            cog_module.repo, "reschedule",
            lambda reminder_id, *, from_time: calls.append(reminder_id),
        )
        bot = FakeBot(channel=None, user=None)

        with caplog.at_level("WARNING", logger="zephyr.cogs.reminders"):
            _deliver(RemindersCog(bot), _row(guild_id=None, repeat_every_seconds=86400))

        assert calls == []

    def test_a_reschedule_failure_does_not_fail_the_delivery(self, monkeypatch, caplog):
        def boom(*_args, **_kwargs):
            raise RuntimeError("db gone")

        monkeypatch.setattr(cog_module.repo, "reschedule", boom)
        channel = FakeChannel()

        with caplog.at_level("ERROR", logger="zephyr.cogs.reminders"):
            assert _deliver(RemindersCog(FakeBot(channel)), _row(repeat_every_seconds=86400)) is True
        assert len(channel.sent) == 1


class _Response:
    """The minimum discord.Forbidden needs to be constructed."""

    def __init__(self, status):
        self.status = status
        self.reason = "Forbidden"


class TestTheZoneLookup:
    def test_a_user_with_no_timezone_gets_utc(self, monkeypatch):
        monkeypatch.setattr(cog_module, "read_bot_user", lambda _id: None)
        assert asyncio.run(RemindersCog(FakeBot())._zone_for(1)) == "UTC"

    def test_a_stored_zone_is_used(self, monkeypatch):
        monkeypatch.setattr(cog_module, "read_bot_user", lambda _id: {"timezone": "Asia/Manila"})
        assert asyncio.run(RemindersCog(FakeBot())._zone_for(1)) == "Asia/Manila"

    def test_a_nonsense_zone_falls_back_rather_than_raising(self, monkeypatch):
        """The column is a bare string with no constraint, so a bad value is
        reachable -- and a ZoneInfoNotFoundError here would fail /remindme."""
        monkeypatch.setattr(cog_module, "read_bot_user", lambda _id: {"timezone": "Mars/Olympus"})
        assert asyncio.run(RemindersCog(FakeBot())._zone_for(1)) == "UTC"

    def test_a_database_failure_falls_back_rather_than_raising(self, monkeypatch, caplog):
        def boom(_id):
            raise RuntimeError("no db")

        monkeypatch.setattr(cog_module, "read_bot_user", boom)
        with caplog.at_level("WARNING", logger="zephyr.cogs.reminders"):
            assert asyncio.run(RemindersCog(FakeBot())._zone_for(1)) == "UTC"


class TestTheCogIsRegistered:
    def test_reminders_is_enabled(self):
        from zephyr import config

        assert "reminders" in config.ENABLED_COGS
