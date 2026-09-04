"""Activity: the level curve, the additive flush, and the bounded accumulator.

Three things here would be wrong in ways nobody notices for weeks.

The curve's boundaries: somebody sitting on exactly 300 XP being told they are
level 1 is the case people report, and it comes from taking the closed-form
inverse of a quadratic through a float.

The flush being **additive**. The batch is a delta, so an assigning upsert would
reset every total to the batch size on the first flush after a restart -- and
would look fine in a test that only ever flushed once.

The accumulator being **bounded**. `_pending` grows with traffic between ticks,
and the moment that matters is when the database is unreachable: without the cap
and without the loop-error handler, the dictionary grows until the process dies,
hours after the actual failure.
"""

import asyncio
import time
from types import SimpleNamespace

import discord
import pytest

from zephyr.cogs import activity as cog_module
from zephyr.cogs.activity import (
    XP_COOLDOWN_SECONDS,
    ActivityCog,
    render_bar,
)
from zephyr.db import activity as repo


# ---------------------------------------------------------------------------
# The curve
# ---------------------------------------------------------------------------


class TestTheLevelCurve:
    def test_the_thresholds_are_the_documented_ones(self):
        assert repo.xp_for_level(0) == 0
        assert repo.xp_for_level(1) == 100
        assert repo.xp_for_level(2) == 300
        assert repo.xp_for_level(3) == 600

    @pytest.mark.parametrize(
        "xp,level",
        [(0, 0), (99, 0), (100, 1), (299, 1), (300, 2), (599, 2), (600, 3)],
    )
    def test_a_level_is_awarded_exactly_at_its_threshold(self, xp, level):
        """The boundaries, one either side of each.

        The inverse of a quadratic goes through a float, and being off by one at
        exactly the boundary is the case somebody reports -- 300 XP and told
        they are level 1.
        """
        assert repo.level_for_xp(xp) == level

    def test_the_curve_is_exact_a_long_way_out(self):
        """Float error grows with the input, so the correction loops have to
        hold at levels nobody will reach in a week of testing."""
        for level in (10, 50, 200, 1000):
            assert repo.level_for_xp(repo.xp_for_level(level)) == level
            assert repo.level_for_xp(repo.xp_for_level(level) - 1) == level - 1

    def test_negative_xp_is_level_zero_not_an_error(self):
        assert repo.level_for_xp(-500) == 0

    def test_progress_reports_the_position_within_the_level(self):
        level, into, needed = repo.progress(150)

        assert (level, into, needed) == (1, 50, 200)

    def test_progress_at_a_boundary_starts_the_next_level_at_zero(self):
        assert repo.progress(300) == (2, 0, 300)


class TestRenderBar:
    def test_an_empty_level_renders_empty(self):
        assert render_bar(0, 100) == "░" * 12

    def test_a_full_level_renders_full(self):
        assert render_bar(100, 100) == "█" * 12

    def test_a_zero_width_level_does_not_divide_by_zero(self):
        assert render_bar(0, 0) == "█" * 12


# ---------------------------------------------------------------------------
# The flush
# ---------------------------------------------------------------------------


class TestFlush:
    def test_an_empty_batch_does_nothing(self, db_url):
        assert repo.flush({}, database_url=db_url) == {"touched": 0, "level_ups": []}

    def test_a_batch_creates_totals(self, db_url):
        repo.flush({("1", "7"): 3}, database_url=db_url)

        member = repo.get_member("1", "7", database_url=db_url)

        assert member["messages"] == 3
        assert member["xp"] == 3 * repo.XP_PER_MESSAGE

    def test_flushes_add_rather_than_replace(self, db_url):
        """The batch is a delta, not a state.

        An assigning upsert would reset the stored total to the batch size on
        the first flush after a restart -- and would pass a test that only
        flushed once.
        """
        repo.flush({("1", "7"): 3}, database_url=db_url)
        repo.flush({("1", "7"): 2}, database_url=db_url)

        assert repo.get_member("1", "7", database_url=db_url)["messages"] == 5

    def test_a_zero_count_is_skipped(self, db_url):
        repo.flush({("1", "7"): 0}, database_url=db_url)

        assert repo.get_member("1", "7", database_url=db_url) is None

    def test_it_reports_a_level_crossing(self, db_url):
        """From the stored total, not the delta: announcing from the in-memory
        count would announce a level the database does not agree with, and
        again after a restart."""
        result = repo.flush({("1", "7"): 10}, database_url=db_url)

        assert result["level_ups"] == [{"guild_id": "1", "user_id": "7", "level": 1}]

    def test_it_reports_no_crossing_when_the_level_is_unchanged(self, db_url):
        repo.flush({("1", "7"): 10}, database_url=db_url)

        assert repo.flush({("1", "7"): 1}, database_url=db_url)["level_ups"] == []

    def test_a_crossing_is_measured_against_what_was_stored(self, db_url):
        """Which is why the previous XP is read before the upsert.

        Ten messages take a fresh member from 0 to level 1; the same ten from a
        member already at level 1 must not report level 1 again.
        """
        repo.flush({("1", "7"): 10}, database_url=db_url)
        second = repo.flush({("1", "7"): 20}, database_url=db_url)

        assert [crossing["level"] for crossing in second["level_ups"]] == [2]

    def test_a_multi_level_jump_reports_only_the_level_reached(self, db_url):
        result = repo.flush({("1", "7"): 60}, database_url=db_url)

        assert [crossing["level"] for crossing in result["level_ups"]] == [3]

    def test_one_batch_covers_several_guilds_and_members(self, db_url):
        result = repo.flush({("1", "7"): 1, ("1", "8"): 1, ("2", "7"): 1}, database_url=db_url)

        assert result["touched"] == 3
        assert repo.get_member("1", "7", database_url=db_url)["messages"] == 1
        assert repo.get_member("2", "7", database_url=db_url)["messages"] == 1


class TestDailySummary:
    def test_it_counts_messages_and_distinct_speakers(self, db_url):
        """Both from one table. `active` is a row count and `messages` is the
        sum of those rows, which is why there is no separate rollup table to
        keep in step."""
        repo.flush({("1", "7"): 3, ("1", "8"): 2}, database_url=db_url)
        day = _today()

        summary = repo.daily_summary("1", day, database_url=db_url)

        assert summary == {"day": day, "active": 2, "messages": 5}

    def test_a_distinct_count_is_not_derived_from_increments(self, db_url):
        """The reason activity_daily_users exists at all.

        Two flushes from the same person are five messages from one speaker, not
        from two -- a per-day total could never tell those apart.
        """
        repo.flush({("1", "7"): 3}, database_url=db_url)
        repo.flush({("1", "7"): 2}, database_url=db_url)

        summary = repo.daily_summary("1", _today(), database_url=db_url)

        assert summary["active"] == 1
        assert summary["messages"] == 5

    def test_a_quiet_day_is_zeroes_not_an_error(self, db_url):
        assert repo.daily_summary("1", "1999-01-01", database_url=db_url)["messages"] == 0


def _today():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class TestLeaderboard:
    def test_it_orders_by_xp(self, db_url):
        repo.flush({("1", "7"): 1, ("1", "8"): 5, ("1", "9"): 3}, database_url=db_url)

        assert [row["user_id"] for row in repo.leaderboard("1", database_url=db_url)] == [
            "8", "9", "7"
        ]

    def test_it_never_includes_another_guild(self, db_url):
        repo.flush({("2", "7"): 9}, database_url=db_url)

        assert repo.leaderboard("1", database_url=db_url) == []

    def test_the_limit_is_capped(self, db_url):
        """Reachable from a slash command, and an embed cannot render an
        unbounded list anyway."""
        repo.flush({("1", str(index)): 1 for index in range(40)}, database_url=db_url)

        assert len(repo.leaderboard("1", limit=1000, database_url=db_url)) == 25


class TestRankOf:
    def test_it_is_one_based(self, db_url):
        repo.flush({("1", "7"): 5}, database_url=db_url)

        assert repo.rank_of("1", "7", database_url=db_url) == 1

    def test_it_counts_everybody_ahead_not_a_page(self, db_url):
        """A member in position 4,000 is a real case, and a page-based rank
        would either be wrong or page the whole table to find them."""
        repo.flush({("1", str(index)): index + 1 for index in range(30)}, database_url=db_url)

        # User "0" has the fewest messages, so is last of thirty.
        assert repo.rank_of("1", "0", database_url=db_url) == 30

    def test_somebody_with_no_row_has_no_rank(self, db_url):
        assert repo.rank_of("1", "404", database_url=db_url) is None


class TestConfig:
    def test_tracking_is_off_until_it_is_turned_on(self, db_url):
        """Leveling counts every message every member sends. Switching that on
        for existing servers without being asked would start collecting it
        silently."""
        assert repo.read_config("1", database_url=db_url) is None
        assert repo.read_all_configs(database_url=db_url) == {}

    def test_announcing_is_the_default_once_tracking_is_on(self, db_url):
        """A level nobody is told about is a number in a database."""
        repo.write_config("1", {"enabled": True}, database_url=db_url)

        assert repo.read_config("1", database_url=db_url)["announce_level_ups"] is True

    def test_announcing_can_be_turned_off_explicitly(self, db_url):
        repo.write_config("1", {"enabled": True, "announce_level_ups": False}, database_url=db_url)

        assert repo.read_config("1", database_url=db_url)["announce_level_ups"] is False

    def test_only_enabled_guilds_are_cached(self, db_url):
        repo.write_config("1", {"enabled": True}, database_url=db_url)
        repo.write_config("2", {"announce_level_ups": True}, database_url=db_url)

        assert set(repo.read_all_configs(database_url=db_url)) == {"1"}

    def test_ignored_channels_round_trip_as_strings(self, db_url):
        repo.write_config(
            "1", {"enabled": True, "ignored_channel_ids": [11]}, database_url=db_url
        )

        assert repo.read_all_configs(database_url=db_url)["1"]["ignored_channel_ids"] == ["11"]

    def test_disabling_keeps_the_totals(self, db_url):
        """So turning it back on resumes rather than restarting from zero."""
        repo.flush({("1", "7"): 5}, database_url=db_url)
        repo.write_config("1", {"enabled": False}, database_url=db_url)

        assert repo.get_member("1", "7", database_url=db_url)["messages"] == 5

    def test_leaving_a_guild_forgets_everything(self, db_url):
        repo.write_config("1", {"enabled": True}, database_url=db_url)
        repo.flush({("1", "7"): 5, ("2", "7"): 5}, database_url=db_url)

        assert repo.delete_for_guild("1", database_url=db_url) == 1
        assert repo.read_config("1", database_url=db_url) is None
        assert repo.get_member("2", "7", database_url=db_url) is not None


# ---------------------------------------------------------------------------
# The accumulator
# ---------------------------------------------------------------------------


def _cog(cache=None):
    cog = ActivityCog.__new__(ActivityCog)
    cog.bot = SimpleNamespace(loop=asyncio.get_event_loop_policy().new_event_loop())
    cog._cache = cache if cache is not None else {"1": _config()}
    cog._pending = {}
    cog._cooldowns = {}
    cog._last_channel = {}
    return cog


def _config(**over):
    base = {
        "guild_id": "1", "enabled": True, "announce_channel_id": None,
        "announce_level_ups": True, "ignored_channel_ids": [],
    }
    base.update(over)
    return base


def _message(*, guild_id=1, channel_id=10, author_id=7, bot=False):
    return SimpleNamespace(
        guild=SimpleNamespace(id=guild_id),
        channel=SimpleNamespace(id=channel_id),
        author=SimpleNamespace(id=author_id, bot=bot),
    )


class TestCount:
    def test_a_message_is_accumulated_in_memory(self):
        cog = _cog()

        assert cog.count(_message()) is True
        assert cog._pending == {("1", "7"): 1}

    def test_nothing_is_written_on_a_message(self, monkeypatch):
        """The single constraint this cog is built around.

        on_message fires for every message in every guild, and a write there
        would turn a busy weekend into a write per message per guild -- for a
        leaderboard nobody reads more than once an hour.
        """
        def boom(*_args, **_kwargs):
            raise AssertionError("the message path must not touch the database")

        for name in ("flush", "read_config", "read_all_configs", "get_member"):
            monkeypatch.setattr(cog_module.repo, name, boom)

        assert _cog().count(_message()) is True

    def test_a_dm_is_not_counted(self):
        message = _message()
        message.guild = None

        assert _cog().count(message) is False

    def test_a_bot_is_not_counted(self):
        assert _cog().count(_message(bot=True)) is False

    def test_an_unconfigured_guild_is_not_counted(self):
        assert _cog(cache={}).count(_message()) is False

    def test_a_disabled_guild_is_not_counted(self):
        assert _cog({"1": _config(enabled=False)}).count(_message()) is False

    def test_an_ignored_channel_is_not_counted(self):
        cog = _cog({"1": _config(ignored_channel_ids=["10"])})

        assert cog.count(_message(channel_id=10)) is False

    def test_the_cooldown_stops_a_second_message(self):
        """Without it, XP measures how fast somebody can type rather than how
        much they take part, and the leaderboard records who spammed most."""
        cog = _cog()

        assert cog.count(_message()) is True
        assert cog.count(_message()) is False
        assert cog._pending == {("1", "7"): 1}

    def test_the_cooldown_expires(self, monkeypatch):
        cog = _cog()
        clock = {"now": 1000.0}
        monkeypatch.setattr(cog_module.time, "monotonic", lambda: clock["now"])

        assert cog.count(_message()) is True
        clock["now"] += XP_COOLDOWN_SECONDS + 1
        assert cog.count(_message()) is True
        assert cog._pending == {("1", "7"): 2}

    def test_the_cooldown_is_per_person(self):
        cog = _cog()
        cog.count(_message(author_id=7))

        assert cog.count(_message(author_id=8)) is True

    def test_the_cooldown_is_per_guild(self):
        cog = _cog({"1": _config(), "2": _config(guild_id="2")})
        cog.count(_message(guild_id=1))

        assert cog.count(_message(guild_id=2)) is True

    def test_the_channel_is_remembered_for_the_announcement(self):
        cog = _cog()
        cog.count(_message(channel_id=55))

        assert cog._last_channel == {("1", "7"): "55"}

    def test_the_cooldown_map_is_bounded(self, monkeypatch):
        """It is keyed per member per guild and would otherwise hold an entry
        for everybody who has ever spoken."""
        monkeypatch.setattr(cog_module, "MAX_COOLDOWN_ENTRIES", 3)
        cog = _cog()
        for author_id in range(5):
            cog.count(_message(author_id=author_id))

        assert len(cog._cooldowns) <= 3

    def test_reaching_the_cap_forces_a_flush(self, monkeypatch):
        """The bound that keeps an unreachable database from becoming an
        out-of-memory failure: without it _pending grows between ticks, and a
        failing flush means it never shrinks."""
        monkeypatch.setattr(cog_module, "MAX_PENDING", 3)
        flushed = []
        cog = _cog()
        cog.bot = SimpleNamespace(loop=SimpleNamespace(create_task=flushed.append))

        for author_id in range(3):
            cog.count(_message(author_id=author_id))

        assert len(flushed) == 1
        # Closed so the un-awaited coroutine does not warn.
        flushed[0].close()


class TestTheCogFlush:
    def test_the_accumulator_is_swapped_before_the_write(self, monkeypatch):
        """A message arriving mid-flush must land in the new dictionary rather
        than in the batch being written, or it is counted twice."""
        seen = {}

        def slow_flush(batch):
            seen["batch"] = dict(batch)
            # As if a message arrived while the write was in flight.
            cog.count(_message(author_id=99))
            return {"touched": len(batch), "level_ups": []}

        monkeypatch.setattr(cog_module.repo, "flush", slow_flush)
        cog = _cog()
        cog.count(_message(author_id=7))

        asyncio.run(cog._flush())

        assert seen["batch"] == {("1", "7"): 1}
        assert cog._pending == {("1", "99"): 1}

    def test_an_empty_accumulator_writes_nothing(self, monkeypatch):
        def boom(_batch):
            raise AssertionError("must not write an empty batch")

        monkeypatch.setattr(cog_module.repo, "flush", boom)

        assert asyncio.run(_cog()._flush()) == 0

    def test_a_failed_flush_drops_the_batch_rather_than_growing_it(self, monkeypatch, caplog):
        """Merging it back is the obvious choice and the wrong one: a database
        that is down stays down for minutes, and a batch that keeps growing
        while being retried is exactly the unbounded growth the cap exists to
        prevent."""
        def boom(_batch):
            raise RuntimeError("no db")

        monkeypatch.setattr(cog_module.repo, "flush", boom)
        cog = _cog()
        cog.count(_message())

        with caplog.at_level("ERROR", logger="zephyr.cogs.activity"):
            assert asyncio.run(cog._flush()) == 0

        assert cog._pending == {}

    def test_a_level_up_is_announced_where_it_was_earned(self, monkeypatch):
        sent = []
        channel = SimpleNamespace(
            send=lambda content=None, **kwargs: _resolved(sent.append((content, kwargs)))
        )
        monkeypatch.setattr(
            cog_module.repo, "flush",
            lambda batch: {
                "touched": 1,
                "level_ups": [{"guild_id": "1", "user_id": "7", "level": 2}],
            },
        )
        cog = _cog()
        cog.bot = SimpleNamespace(
            get_guild=lambda _id: SimpleNamespace(get_channel=lambda _cid: channel)
        )
        cog.count(_message(channel_id=55))

        asyncio.run(cog._flush())

        assert "level 2" in sent[0][0]

    def test_a_level_up_never_pings_everyone(self, monkeypatch):
        sent = []
        channel = SimpleNamespace(
            send=lambda content=None, **kwargs: _resolved(sent.append((content, kwargs)))
        )
        monkeypatch.setattr(
            cog_module.repo, "flush",
            lambda batch: {
                "touched": 1,
                "level_ups": [{"guild_id": "1", "user_id": "7", "level": 2}],
            },
        )
        cog = _cog()
        cog.bot = SimpleNamespace(
            get_guild=lambda _id: SimpleNamespace(get_channel=lambda _cid: channel)
        )
        cog.count(_message())

        asyncio.run(cog._flush())
        allowed = sent[0][1]["allowed_mentions"]

        assert allowed.everyone is False
        assert allowed.roles is False

    def test_a_guild_that_turned_announcements_off_is_silent(self, monkeypatch):
        sent = []
        channel = SimpleNamespace(
            send=lambda content=None, **kwargs: _resolved(sent.append(content))
        )
        monkeypatch.setattr(
            cog_module.repo, "flush",
            lambda batch: {
                "touched": 1,
                "level_ups": [{"guild_id": "1", "user_id": "7", "level": 2}],
            },
        )
        cog = _cog({"1": _config(announce_level_ups=False)})
        cog.bot = SimpleNamespace(
            get_guild=lambda _id: SimpleNamespace(get_channel=lambda _cid: channel)
        )
        cog.count(_message())

        asyncio.run(cog._flush())

        assert sent == []

    def test_a_configured_channel_overrides_where_it_was_earned(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            cog_module.repo, "flush",
            lambda batch: {
                "touched": 1,
                "level_ups": [{"guild_id": "1", "user_id": "7", "level": 2}],
            },
        )
        cog = _cog({"1": _config(announce_channel_id="777")})
        cog.bot = SimpleNamespace(
            get_guild=lambda _id: SimpleNamespace(
                get_channel=lambda cid: (
                    seen.append(cid)
                    or SimpleNamespace(send=lambda content=None, **kwargs: _resolved(None))
                )
            )
        )
        cog.count(_message(channel_id=55))

        asyncio.run(cog._flush())

        assert seen == [777]

    def test_a_failed_announcement_does_not_fail_the_flush(self, monkeypatch, caplog):
        channel = SimpleNamespace(
            send=lambda content=None, **kwargs: _raising(
                discord.Forbidden(SimpleNamespace(status=403, reason="no"), "no")
            )
        )
        monkeypatch.setattr(
            cog_module.repo, "flush",
            lambda batch: {
                "touched": 1,
                "level_ups": [{"guild_id": "1", "user_id": "7", "level": 2}],
            },
        )
        cog = _cog()
        cog.bot = SimpleNamespace(
            get_guild=lambda _id: SimpleNamespace(get_channel=lambda _cid: channel)
        )
        cog.count(_message())

        with caplog.at_level("WARNING", logger="zephyr.cogs.activity"):
            assert asyncio.run(cog._flush()) == 1


def _resolved(value):
    async def coro():
        return value

    return coro()


def _raising(error):
    async def coro():
        raise error

    return coro()


class TestTheLoopSurvivesAnError:
    def test_the_flush_loop_has_an_error_handler(self):
        """A raising tasks.loop is cancelled, not retried.

        Without a handler one unexpected error stops flushing silently and
        _pending grows to an out-of-memory failure hours later, with nothing in
        the log connecting the two.
        """
        # A loop with no handler keeps `Loop._error` bound to itself; one with
        # a handler holds the cog's own function. `_refresh_loop` is the
        # untouched control, so this cannot pass by comparing against nothing.
        assert ActivityCog._flush_loop._error.__name__ == "_flush_loop_error"
        assert ActivityCog._refresh_loop._error.__name__ == "_error"

    def test_the_handler_restarts_the_loop(self, caplog):
        restarted = []
        cog = _cog()
        cog._flush_loop = SimpleNamespace(restart=lambda: restarted.append(True))

        with caplog.at_level("ERROR", logger="zephyr.cogs.activity"):
            asyncio.run(ActivityCog._flush_loop_error(cog, RuntimeError("boom")))

        assert restarted == [True]


class TestTheCogIsRegistered:
    def test_activity_is_enabled(self):
        from zephyr import config

        assert "activity" in config.ENABLED_COGS
