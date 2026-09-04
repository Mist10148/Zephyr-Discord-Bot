"""The DJ lock, the vote-skip ratio, and persisted 24/7.

`interaction_check` is the security-relevant half. Before 15.4, `_authorize`
gated the *bridge* -- every dashboard and now-playing button -- while the slash
commands went through `get_voice_state` with no authorization at all, so a server
that configured a DJ role got a DJ-gated dashboard and an ungated `/stop`. The
specs below enumerate who passes the lock and who does not, rather than sampling.

`_skip_threshold` is pure over the state, which is why it can be tested here with
a hand-built channel and no bot.
"""

import asyncio
from types import SimpleNamespace

import pytest

from zephyr.cogs.music import (
    DEFAULT_SKIP_RATIO,
    DJ_EXEMPT_COMMANDS,
    MAX_SKIP_RATIO,
    MIN_SKIP_RATIO,
    MusicCog,
    VoiceState,
)
from zephyr.core.errors import Refused, user_facing_message
from zephyr.db.guild_settings import read_music_policies, write_guild_settings


# ---------------------------------------------------------------------------
# The vote-skip threshold
# ---------------------------------------------------------------------------


def _state(*, listeners=0, ratio=None, connected=True):
    state = VoiceState.__new__(VoiceState)
    state.skip_ratio = ratio if ratio is not None else DEFAULT_SKIP_RATIO
    members = [SimpleNamespace(bot=False) for _ in range(listeners)]
    # A bot in the channel must not count towards a human vote, so Zephyr
    # itself is always present here.
    members.append(SimpleNamespace(bot=True))
    state.voice = (
        SimpleNamespace(channel=SimpleNamespace(members=members)) if connected else None
    )
    return state


class TestSkipThreshold:
    def test_the_default_is_half_the_listeners_rounded_up(self):
        assert _state(listeners=4)._skip_threshold() == 2
        assert _state(listeners=5)._skip_threshold() == 3

    def test_bots_do_not_count(self):
        """A channel of one person plus Zephyr needs one vote, not one of two."""
        assert _state(listeners=1)._skip_threshold() == 1

    def test_a_configured_ratio_is_used(self):
        assert _state(listeners=10, ratio=0.3)._skip_threshold() == 3
        assert _state(listeners=10, ratio=0.9)._skip_threshold() == 9

    def test_a_full_ratio_cannot_exceed_the_people_who_could_vote(self):
        """ceil(3 * 1.0) is 3, but ceil of a fractional ratio can round past the
        headcount -- and a threshold above it makes /skip a command that can
        never succeed."""
        assert _state(listeners=3, ratio=1.0)._skip_threshold() == 3
        assert _state(listeners=3, ratio=0.99)._skip_threshold() == 3

    def test_it_is_never_zero(self):
        assert _state(listeners=1, ratio=0.05)._skip_threshold() == 1

    def test_an_empty_channel_needs_one_vote(self):
        assert _state(listeners=0)._skip_threshold() == 1

    def test_a_disconnected_state_needs_one_vote(self):
        assert _state(connected=False)._skip_threshold() == 1


# ---------------------------------------------------------------------------
# The DJ lock
# ---------------------------------------------------------------------------


def _cog(*, dj_only=False, dj_role_id=None):
    cog = MusicCog.__new__(MusicCog)
    cog._music_policy = {1: {"dj_only": dj_only}}
    cog._dj_role_ids = {1: dj_role_id} if dj_role_id else {}
    cog.voice_states = {}
    return cog


def _interaction(command_name, *, manage_guild=False, roles=()):
    return SimpleNamespace(
        command=SimpleNamespace(name=command_name),
        guild=SimpleNamespace(id=1),
        user=SimpleNamespace(
            id=7,
            guild_permissions=SimpleNamespace(manage_guild=manage_guild),
            roles=[SimpleNamespace(id=role_id) for role_id in roles],
        ),
    )


def _check(cog, interaction):
    return asyncio.run(cog.interaction_check(interaction))


class TestTheDJLock:
    def test_it_is_off_by_default(self):
        """Turning it on for existing servers would silently take the player
        away from everybody who could use it yesterday."""
        assert _check(_cog(), _interaction("stop")) is True

    def test_a_locked_player_refuses_a_stranger(self):
        with pytest.raises(Refused):
            _check(_cog(dj_only=True, dj_role_id="99"), _interaction("stop"))

    def test_the_dj_role_passes(self):
        cog = _cog(dj_only=True, dj_role_id="99")
        assert _check(cog, _interaction("stop", roles=[99])) is True

    def test_manage_server_always_passes(self):
        cog = _cog(dj_only=True, dj_role_id="99")
        assert _check(cog, _interaction("stop", manage_guild=True)) is True

    def test_a_lock_with_no_dj_role_admits_only_manage_server(self):
        """A deliberate reading of "lock the player", not an oversight -- and
        the refusal says so, because it is a much stricter setting than it
        sounds."""
        cog = _cog(dj_only=True)

        with pytest.raises(Refused) as raised:
            _check(cog, _interaction("stop", roles=[99]))
        assert "no DJ role is set" in str(raised.value)
        assert _check(cog, _interaction("stop", manage_guild=True)) is True

    @pytest.mark.parametrize("command", sorted(DJ_EXEMPT_COMMANDS))
    def test_reading_the_queue_is_never_locked(self, command):
        """A lock that hid /queue and /now would make the bot look broken to
        everybody who is only listening."""
        assert _check(_cog(dj_only=True), _interaction(command)) is True

    @pytest.mark.parametrize(
        "command", ["play", "skip", "stop", "volume", "pause", "seek", "shuffle", "247"]
    )
    def test_every_mutating_command_is_locked(self, command):
        with pytest.raises(Refused):
            _check(_cog(dj_only=True, dj_role_id="99"), _interaction(command))

    def test_a_command_added_later_is_locked_by_default(self):
        """The set is derived as the complement of an exemption list on purpose.

        The failure mode of getting that wrong is "a DJ had to press it", not "a
        new command silently bypassed the lock".
        """
        with pytest.raises(Refused):
            _check(_cog(dj_only=True, dj_role_id="99"), _interaction("some-future-command"))

    def test_a_dm_is_never_locked(self):
        interaction = _interaction("play")
        interaction.guild = None
        assert _check(_cog(dj_only=True), interaction) is True

    def test_an_unknown_command_is_not_locked(self):
        interaction = _interaction("play")
        interaction.command = None
        assert _check(_cog(dj_only=True), interaction) is True


class TestTheRefusalReachesTheUser:
    def test_a_refusal_carries_its_own_sentence(self):
        """A bare CheckFailure renders as "You cannot use that command here.",
        which sends somebody to ask an administrator why the bot is broken."""
        assert user_facing_message(Refused("the DJ role is required")) == "❌ the DJ role is required"

    def test_a_bare_check_failure_still_gets_the_generic_line(self):
        from discord import app_commands

        assert user_facing_message(app_commands.CheckFailure()) == "You cannot use that command here."


# ---------------------------------------------------------------------------
# Applying a stored policy
# ---------------------------------------------------------------------------


def _policy_cog(policies):
    cog = MusicCog.__new__(MusicCog)
    cog._music_policy = policies
    cog._dj_role_ids = {}
    cog.voice_states = {}
    return cog


def _bare_state(guild_id=1):
    state = VoiceState.__new__(VoiceState)
    state.guild_id = guild_id
    state.skip_ratio = DEFAULT_SKIP_RATIO
    state._247_enabled = False
    return state


class TestApplyPolicy:
    def test_a_stored_ratio_reaches_the_state(self):
        state = _bare_state()
        _policy_cog({1: {"vote_skip_ratio": 0.25}}).apply_policy(state)

        assert state.skip_ratio == 0.25

    def test_an_absurd_stored_ratio_is_clamped(self):
        """The column is a bare float with no constraint, so a hand-written row
        or an older client can put anything in it."""
        high, low = _bare_state(), _bare_state()
        _policy_cog({1: {"vote_skip_ratio": 9.0}}).apply_policy(high)
        _policy_cog({1: {"vote_skip_ratio": 0.0001}}).apply_policy(low)

        assert high.skip_ratio == MAX_SKIP_RATIO
        assert low.skip_ratio == MIN_SKIP_RATIO

    def test_stored_24_7_is_restored(self):
        """The flag used to live only in memory, so "24/7" meant "until the next
        deploy"."""
        state = _bare_state()
        _policy_cog({1: {"always_on": True}}).apply_policy(state)

        assert state._247_enabled is True

    def test_turning_24_7_off_turns_it_off_on_a_live_state(self):
        """Assigned rather than or-ed: an or would make the dashboard's off
        switch do nothing until the player was torn down."""
        state = _bare_state()
        state._247_enabled = True
        _policy_cog({1: {"always_on": False}}).apply_policy(state)

        assert state._247_enabled is False

    def test_an_unconfigured_guild_keeps_the_defaults(self):
        state = _bare_state(guild_id=404)
        _policy_cog({1: {"vote_skip_ratio": 0.9}}).apply_policy(state)

        assert state.skip_ratio == DEFAULT_SKIP_RATIO
        assert state._247_enabled is False


# ---------------------------------------------------------------------------
# The bulk read
# ---------------------------------------------------------------------------


class TestReadMusicPolicies:
    def test_a_guild_with_nothing_configured_is_absent(self, db_url):
        """The cache's defaults then apply, and the read stays proportional to
        the number of guilds that have actually asked for something."""
        write_guild_settings("1", {"prefix": "!"}, database_url=db_url)

        assert read_music_policies(database_url=db_url) == {}

    def test_each_setting_alone_is_enough_to_appear(self, db_url):
        write_guild_settings("1", {"dj_only": True}, database_url=db_url)
        write_guild_settings("2", {"vote_skip_ratio": 0.3}, database_url=db_url)
        write_guild_settings("3", {"always_on": True}, database_url=db_url)

        assert set(read_music_policies(database_url=db_url)) == {"1", "2", "3"}

    def test_it_reads_back_what_was_written(self, db_url):
        write_guild_settings(
            "1",
            {"dj_only": True, "vote_skip_ratio": 0.75, "always_on": True,
             "always_on_channel_id": "55"},
            database_url=db_url,
        )

        policy = read_music_policies(database_url=db_url)["1"]

        assert policy == {
            "dj_only": True, "vote_skip_ratio": 0.75,
            "always_on": True, "always_on_channel_id": "55",
        }

    def test_a_null_flag_reads_as_false_not_none(self, db_url):
        """The columns are nullable because 0005 had to add them without a
        server_default, so every consumer would otherwise have to handle three
        states for a two-state setting."""
        write_guild_settings("1", {"vote_skip_ratio": 0.3}, database_url=db_url)

        assert read_music_policies(database_url=db_url)["1"]["dj_only"] is False


class TestTheSettingsAreWritable:
    @pytest.mark.parametrize(
        "column", ["dj_only", "vote_skip_ratio", "always_on", "always_on_channel_id"]
    )
    def test_the_dashboard_can_set_them(self, column):
        from zephyr.db.guild_settings import WRITABLE_COLUMNS

        assert column in WRITABLE_COLUMNS
