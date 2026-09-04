"""Moderation: the hierarchy boundary, the case allocation, and the modlog.

`hierarchy_refusal` gets the most tests here because it is the only *security*
code in the feature. Discord checks a bot action against the **bot's** role, not
the caller's, so a junior moderator with Ban Members could ban an administrator
through Zephyr if this function let them -- the API would happily allow it. Every
way that could go wrong is enumerated below rather than sampled.

Async methods are driven with asyncio.run, matching test_weather_scheduler.py.
"""

import asyncio
from types import SimpleNamespace

import discord
import pytest

from zephyr.cogs import moderation as cog_module
from zephyr.cogs.moderation import (
    ModerationCog,
    case_embed,
    hierarchy_refusal,
    humanise_duration,
    parse_duration,
)
from zephyr.db import mod_cases as repo


class _Role:
    """Ordered like discord.Role: comparison is by position."""

    def __init__(self, position):
        self.position = position

    def __ge__(self, other):
        return self.position >= other.position

    def __lt__(self, other):
        return self.position < other.position


def _guild(owner_id=999):
    return SimpleNamespace(id=1, name="A Server", owner_id=owner_id)


def _member(user_id, position, *, guild=None):
    return SimpleNamespace(
        id=user_id, top_role=_Role(position), guild=guild or _guild(), mention=f"<@{user_id}>"
    )


ME = _member(50, 90)


# ---------------------------------------------------------------------------
# The security boundary
# ---------------------------------------------------------------------------


class TestHierarchyRefusal:
    def test_a_higher_moderator_may_act(self):
        assert hierarchy_refusal(_member(1, 50), _member(2, 10), ME) is None

    def test_a_lower_moderator_may_not(self):
        """The whole reason this function exists.

        Discord permits the action because it checks the *bot's* role, so
        without this a Ban Members holder at role position 10 could ban an
        administrator at position 80.
        """
        refusal = hierarchy_refusal(_member(1, 10), _member(2, 80), ME)
        assert refusal is not None
        assert "not below yours" in refusal

    def test_an_equal_role_may_not_act(self):
        """Discord treats equal positions as "cannot act", and so must this.

        A strict `>` on the wrong side is exactly how two moderators with the
        same role end up able to ban each other.
        """
        assert hierarchy_refusal(_member(1, 40), _member(2, 40), ME) is not None

    def test_nobody_may_moderate_themselves(self):
        actor = _member(1, 50)
        assert "yourself" in hierarchy_refusal(actor, actor, ME)

    def test_nobody_may_moderate_the_bot_through_the_bot(self):
        assert "myself" in hierarchy_refusal(_member(1, 80), _member(50, 90), ME)

    def test_nobody_may_moderate_the_owner(self):
        """Not even another administrator, and not the owner's own roles.

        A server owner's roles do not necessarily outrank anybody's -- an owner
        with no roles at all sits at position 0 -- so a check that relied only
        on the role comparison would let an administrator ban them.
        """
        guild = _guild(owner_id=7)
        owner = _member(7, 0, guild=guild)
        admin = _member(1, 80, guild=guild)

        assert "server owner" in hierarchy_refusal(admin, owner, ME)

    def test_the_owner_may_act_regardless_of_roles(self):
        guild = _guild(owner_id=7)
        owner = _member(7, 0, guild=guild)
        member = _member(2, 60, guild=guild)

        assert hierarchy_refusal(owner, member, ME) is None

    def test_the_bots_own_reach_is_checked_first(self):
        """And reported differently, because it is fixable by moving a role.

        Telling an administrator "their role is not below yours" when the real
        problem is Zephyr's position sends them to change the wrong thing.
        """
        low_bot = _member(50, 5)
        refusal = hierarchy_refusal(_member(1, 80), _member(2, 60), low_bot)

        assert "Move Zephyr's role up" in refusal

    def test_the_owner_exemption_does_not_bypass_the_bots_reach(self):
        """An owner still cannot make Discord do the impossible."""
        guild = _guild(owner_id=7)
        owner = _member(7, 0, guild=guild)
        refusal = hierarchy_refusal(owner, _member(2, 60, guild=guild), _member(50, 5))

        assert refusal is not None


# ---------------------------------------------------------------------------
# Parsing and rendering
# ---------------------------------------------------------------------------


class TestParseDuration:
    @pytest.mark.parametrize(
        "text,seconds",
        [("30s", 30), ("10m", 600), ("2h", 7200), ("1d", 86400), ("1w", 604800), (" 10 M ", 600)],
    )
    def test_it_reads_a_simple_duration(self, text, seconds):
        assert parse_duration(text) == seconds

    @pytest.mark.parametrize("text", ["", "10", "m", "soon", "10 fortnights", "1h30m"])
    def test_it_refuses_everything_else(self, text):
        """"1h30m" included: Discord rounds a timeout, so a compound form here
        would promise a precision the platform does not keep."""
        assert parse_duration(text) is None


class TestHumaniseDuration:
    @pytest.mark.parametrize(
        "seconds,text", [(60, "1 minute"), (600, "10 minutes"), (86400, "1 day"), (604800, "1 week")]
    )
    def test_it_names_round_durations(self, seconds, text):
        assert humanise_duration(seconds) == text

    def test_nothing_renders_as_a_dash_not_none(self):
        assert humanise_duration(None) == "—"


class TestCaseEmbed:
    def test_a_missing_reason_says_how_to_add_one(self):
        """Rather than "—": a case with no reason yet is an outstanding task,
        and rendering it as a blank hides that."""
        embed = case_embed(_case(reason=None))
        reason = [field for field in embed.fields if field.name == "Reason"][0]

        assert "/reason 3" in reason.value

    def test_the_target_id_is_shown_even_when_the_name_is_known(self):
        """A display name is not an identifier and changes freely; the id is
        what a moderator needs to act on somebody who already left."""
        embed = case_embed(_case(target_tag="Someone#0001"))
        user = [field for field in embed.fields if field.name == "User"][0]

        assert "Someone#0001" in user.value
        assert "4242" in user.value

    def test_a_duration_is_shown_only_when_there_is_one(self):
        assert not [f for f in case_embed(_case()).fields if f.name == "Duration"]
        assert [f for f in case_embed(_case(duration_seconds=600)).fields if f.name == "Duration"]


def _case(**over):
    base = {
        "case_number": 3, "action": "warn", "target_id": "4242", "target_tag": None,
        "moderator_id": "77", "reason": "spam", "duration_seconds": None,
        "created_at": "2026-09-05T00:00:00+00:00",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# The repository
# ---------------------------------------------------------------------------


def _record(db_url, **over):
    values = {
        "guild_id": "1", "action": "warn", "target_id": "2", "moderator_id": "3",
        "target_tag": "Someone", "reason": "spam",
    }
    values.update(over)
    return repo.record(database_url=db_url, **values)


class TestRecord:
    def test_the_first_case_in_a_guild_is_number_one(self, db_url):
        assert _record(db_url)["case_number"] == 1

    def test_numbers_are_sequential_within_a_guild(self, db_url):
        assert [_record(db_url)["case_number"] for _ in range(3)] == [1, 2, 3]

    def test_numbering_restarts_per_guild(self, db_url):
        """A moderator says "case 12", and a global counter would both read
        absurdly and leak how busy every other server is."""
        _record(db_url, guild_id="1")
        _record(db_url, guild_id="1")

        assert _record(db_url, guild_id="2")["case_number"] == 1

    def test_an_unknown_action_is_refused(self, db_url):
        """These strings are filtered on, so a typo would be invisible to every
        filter looking for the correct spelling."""
        with pytest.raises(ValueError):
            _record(db_url, action="yeet")

    def test_an_empty_reason_is_stored_as_none(self, db_url):
        """So "has a reason" is one check rather than two."""
        assert _record(db_url, reason="   ")["reason"] is None

    def test_a_long_reason_is_trimmed_on_the_way_in(self, db_url):
        """Trimmed here rather than at render time, so what is stored is what
        will be shown -- an embed field caps at 1024."""
        stored = _record(db_url, reason="x" * 5000)["reason"]
        assert len(stored) == repo.MAX_REASON_CHARS

    def test_it_retries_after_losing_the_allocation_race(self, db_url, monkeypatch):
        """The allocation is a read-then-write race by construction.

        Two moderators acting in the same second read the same maximum. The
        unique constraint rejects the loser, which must retry rather than
        surface an error -- and must not overwrite the winner's case.
        """
        _record(db_url)
        real = repo._next_case_number
        stale = {"used": False}

        def once_stale(connection, guild_id):
            if not stale["used"]:
                stale["used"] = True
                # The number somebody else just took.
                return 1
            return real(connection, guild_id)

        monkeypatch.setattr(repo, "_next_case_number", once_stale)

        assert _record(db_url)["case_number"] == 2
        assert stale["used"] is True
        # And the winner's case is untouched.
        assert repo.get("1", 1, database_url=db_url)["case_number"] == 1

    def test_it_gives_up_rather_than_looping_forever(self, db_url, monkeypatch):
        """A retry that never succeeded would hold a request open indefinitely,
        and the moderator needs to be told the action was not recorded."""
        _record(db_url)
        monkeypatch.setattr(repo, "_next_case_number", lambda connection, guild_id: 1)

        with pytest.raises(RuntimeError):
            _record(db_url)

    def test_a_duplicate_number_is_refused_by_the_database(self, db_url):
        """The premise the retry rests on. Without the unique constraint the
        second insert would succeed and the history would have two case 1s."""
        from sqlalchemy import insert
        from sqlalchemy.exc import IntegrityError

        from zephyr.db.models import ModCase
        from zephyr.db.session import get_engine

        _record(db_url)

        with pytest.raises(IntegrityError):
            with get_engine(db_url).begin() as connection:
                connection.execute(
                    insert(ModCase).values(
                        guild_id="1", case_number=1, action="warn",
                        target_id="2", moderator_id="3",
                    )
                )


class TestGet:
    def test_a_case_is_scoped_to_its_guild(self, db_url):
        """Case numbers restart per guild, so an unscoped lookup would answer
        with another server's case 1."""
        _record(db_url, guild_id="1", reason="ours")
        _record(db_url, guild_id="2", reason="theirs")

        assert repo.get("1", 1, database_url=db_url)["reason"] == "ours"
        assert repo.get("2", 1, database_url=db_url)["reason"] == "theirs"

    def test_a_missing_case_is_none_not_an_error(self, db_url):
        assert repo.get("1", 99, database_url=db_url) is None


class TestRead:
    def test_it_returns_newest_first(self, db_url):
        for _ in range(3):
            _record(db_url)

        page = repo.read("1", database_url=db_url)

        assert [entry["case_number"] for entry in page["entries"]] == [3, 2, 1]

    def test_it_pages_on_the_case_number(self, db_url):
        for _ in range(5):
            _record(db_url)

        first = repo.read("1", limit=2, database_url=db_url)
        second = repo.read("1", limit=2, before_number=first["next_before"], database_url=db_url)

        assert [entry["case_number"] for entry in first["entries"]] == [5, 4]
        assert first["next_before"] == 4
        assert [entry["case_number"] for entry in second["entries"]] == [3, 2]

    def test_the_last_page_reports_no_more(self, db_url):
        _record(db_url)
        assert repo.read("1", limit=5, database_url=db_url)["next_before"] is None

    def test_it_filters_by_target(self, db_url):
        _record(db_url, target_id="2")
        _record(db_url, target_id="9")

        page = repo.read("1", target_id="9", database_url=db_url)

        assert [entry["target_id"] for entry in page["entries"]] == ["9"]

    def test_it_filters_by_action(self, db_url):
        _record(db_url, action="warn")
        _record(db_url, action="ban")

        assert len(repo.read("1", action="ban", database_url=db_url)["entries"]) == 1

    def test_it_never_returns_another_guilds_cases(self, db_url):
        _record(db_url, guild_id="2")
        assert repo.read("1", database_url=db_url)["entries"] == []

    def test_the_limit_is_capped(self, db_url):
        """Reachable from the bridge, so a hand-crafted limit must not be able
        to ask for the whole table."""
        page = repo.read("1", limit=100_000, database_url=db_url)
        assert page["entries"] == []


class TestCountForTarget:
    def test_it_counts_every_case_not_a_page(self, db_url, monkeypatch):
        """A count read from a page would silently stop at MAX_LIMIT, so "this
        is their fourth warning" would start lying at exactly the point it
        matters."""
        monkeypatch.setattr(repo, "MAX_LIMIT", 2)
        for _ in range(5):
            _record(db_url, target_id="2")

        assert repo.count_for_target("1", "2", database_url=db_url) == 5

    def test_it_can_count_one_action(self, db_url):
        _record(db_url, target_id="2", action="warn")
        _record(db_url, target_id="2", action="warn")
        _record(db_url, target_id="2", action="ban")

        assert repo.count_for_target("1", "2", action="warn", database_url=db_url) == 2

    def test_a_stranger_has_no_cases(self, db_url):
        assert repo.count_for_target("1", "404", database_url=db_url) == 0


class TestSetReason:
    def test_it_replaces_the_reason(self, db_url):
        created = _record(db_url, reason=None)
        updated = repo.set_reason("1", created["case_number"], "raid", database_url=db_url)

        assert updated["reason"] == "raid"

    def test_it_is_scoped_to_the_guild(self, db_url):
        _record(db_url, guild_id="1", reason="ours")

        assert repo.set_reason("2", 1, "hijacked", database_url=db_url) is None
        assert repo.get("1", 1, database_url=db_url)["reason"] == "ours"

    def test_a_missing_case_is_none_not_a_silent_success(self, db_url):
        assert repo.set_reason("1", 99, "whatever", database_url=db_url) is None


# ---------------------------------------------------------------------------
# The modlog
# ---------------------------------------------------------------------------


class FakeChannel:
    def __init__(self, *, raises=None, can_send=True):
        self.sent = []
        self._raises = raises
        self._can_send = can_send
        self.name = "mod-log"

    def permissions_for(self, _member):
        return SimpleNamespace(send_messages=self._can_send)

    async def send(self, *, content=None, embed=None):
        if self._raises:
            raise self._raises
        self.sent.append(embed)


class FakeGuild:
    def __init__(self, channel=None):
        self.id = 1
        self.name = "A Server"
        self.owner_id = 999
        self.me = ME
        self._channel = channel

    def get_channel(self, _id):
        return self._channel


class TestPostToModlog:
    def test_a_case_is_posted_to_the_configured_channel(self, monkeypatch):
        monkeypatch.setattr(
            cog_module, "read_guild_settings", lambda _id: {"modlog_channel_id": "77"}
        )
        channel = FakeChannel()
        cog = ModerationCog(SimpleNamespace())

        assert asyncio.run(cog._post_to_modlog(FakeGuild(channel), _case())) is True
        assert channel.sent[0].title.startswith("Case #3")

    def test_an_unconfigured_modlog_is_not_an_error(self, monkeypatch):
        """The modlog is opt-in. Cases are recorded either way."""
        monkeypatch.setattr(cog_module, "read_guild_settings", lambda _id: None)
        cog = ModerationCog(SimpleNamespace())

        assert asyncio.run(cog._post_to_modlog(FakeGuild(), _case())) is False

    def test_a_deleted_modlog_channel_is_logged_not_raised(self, monkeypatch, caplog):
        monkeypatch.setattr(
            cog_module, "read_guild_settings", lambda _id: {"modlog_channel_id": "77"}
        )
        cog = ModerationCog(SimpleNamespace())

        with caplog.at_level("WARNING", logger="zephyr.cogs.moderation"):
            assert asyncio.run(cog._post_to_modlog(FakeGuild(None), _case())) is False
        assert "is gone" in caplog.text

    def test_a_failed_post_does_not_fail_the_command(self, monkeypatch, caplog):
        """The case is already durable at this point.

        Raising here would tell a moderator their ban did not happen, which is
        both false and the kind of thing that gets an action repeated.
        """
        monkeypatch.setattr(
            cog_module, "read_guild_settings", lambda _id: {"modlog_channel_id": "77"}
        )
        channel = FakeChannel(raises=discord.HTTPException(_Response(500), "boom"))
        cog = ModerationCog(SimpleNamespace())

        with caplog.at_level("WARNING", logger="zephyr.cogs.moderation"):
            assert asyncio.run(cog._post_to_modlog(FakeGuild(channel), _case())) is False

    def test_a_settings_read_failure_does_not_fail_the_command(self, monkeypatch, caplog):
        def boom(_id):
            raise RuntimeError("no db")

        monkeypatch.setattr(cog_module, "read_guild_settings", boom)
        cog = ModerationCog(SimpleNamespace())

        with caplog.at_level("ERROR", logger="zephyr.cogs.moderation"):
            assert asyncio.run(cog._post_to_modlog(FakeGuild(), _case())) is False


class _Response:
    def __init__(self, status):
        self.status = status
        self.reason = "Server Error"


class TestTheAuditReason:
    def test_it_names_the_moderator_not_just_the_bot(self):
        """Discord's own audit log is the only place this trace exists.

        Without the moderator's name every action there reads as Zephyr's own
        decision, and "who banned them" becomes unanswerable outside the case
        log.
        """
        interaction = SimpleNamespace(user=SimpleNamespace(id=77, __str__=lambda self: "mod"))
        reason = cog_module._audit_reason(interaction, "raid")

        assert "77" in reason
        assert "raid" in reason


class TestTheCogIsRegistered:
    def test_moderation_is_enabled(self):
        from zephyr import config

        assert "moderation" in config.ENABLED_COGS

    def test_the_bridge_exposes_only_a_reader(self):
        """A privileged write action with no dashboard to drive it would be
        attack surface for no benefit."""
        actions = ModerationCog(SimpleNamespace()).bridge_actions()

        assert set(actions) == {"mod.cases"}

    def test_modlog_channel_id_is_a_writable_setting(self):
        """The dashboard has to be able to set it, or the modlog is
        Discord-only for no reason."""
        from zephyr.db.guild_settings import WRITABLE_COLUMNS

        assert "modlog_channel_id" in WRITABLE_COLUMNS
