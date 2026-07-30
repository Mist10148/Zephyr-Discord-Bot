"""The music cog's side of the web bridge.

No Redis, no gateway and no voice connection: the snapshot is pure projection,
the argument coercion is pure validation, and ``_authorize`` only ever reads a
member object -- which is exactly why it can be a stub here and still be the
real permission check in production.
"""

from unittest.mock import MagicMock

import pytest

from zephyr.cogs.music import (
    SNAPSHOT_QUEUE_LIMIT,
    MusicCog,
    Track,
    VoiceError,
    VoiceState,
    _apply_effects,
    _coerce_float,
)


def _state(guild_id=1, connected=True, playing=True):
    state = VoiceState(MagicMock(), guild_id, channel_id=99)
    if connected:
        state.voice = MagicMock()
        state.voice.is_connected.return_value = True
        state.voice.is_playing.return_value = playing
        state.voice.is_paused.return_value = False
        state.voice.channel.id = 555
        state.voice.channel.name = "General"
    else:
        state.voice = None
    return state


def _cog(states=None, dj_role_id=None):
    """A MusicCog without running __init__ -- it would build a Spotify client."""
    cog = MusicCog.__new__(MusicCog)
    cog.bot = MagicMock()
    cog.voice_states = states or {}
    cog._voice_connect_locks = {}
    cog._dj_role_ids = {1: dj_role_id} if dj_role_id else {}
    return cog


def _member(*, manage_guild=False, roles=(), voice_channel=None, member_id=42):
    member = MagicMock()
    member.id = member_id
    member.guild_permissions.manage_guild = manage_guild
    member.roles = [MagicMock(id=role_id) for role_id in roles]
    member.voice = MagicMock(channel=voice_channel) if voice_channel is not None else None
    return member


def _guild(member=None, guild_id=1):
    guild = MagicMock()
    guild.id = guild_id
    guild.get_member.return_value = member
    return guild


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def test_a_track_payload_stringifies_the_requester_id():
    """Snowflakes exceed Number.MAX_SAFE_INTEGER, so the wire form is a string."""
    payload = Track(title="A", url="http://a", duration_seconds=12, requester_id=900000000000000001).to_payload()

    assert payload["requester_id"] == "900000000000000001"
    assert payload["duration_s"] == 12
    assert set(payload) == {
        "title", "url", "duration_s", "requester_id", "requester_mention",
        "uploader", "thumbnail", "source",
    }


def test_a_snapshot_reports_the_whole_queue_even_though_it_carries_part_of_it():
    state = _state()
    state.current = Track(title="Now", url="http://now", duration_seconds=100)
    for index in range(SNAPSHOT_QUEUE_LIMIT + 20):
        state.songs.put_nowait(Track(title=f"T{index}", url="http://t", duration_seconds=10))

    snapshot = state.snapshot()

    assert len(snapshot["queue"]) == SNAPSHOT_QUEUE_LIMIT
    assert snapshot["queue_length"] == SNAPSHOT_QUEUE_LIMIT + 20
    assert snapshot["queue_duration_s"] == (SNAPSHOT_QUEUE_LIMIT + 20) * 10
    assert snapshot["track"]["title"] == "Now"
    assert snapshot["voice_channel_name"] == "General"


def test_a_disconnected_snapshot_says_so_rather_than_omitting_the_field():
    snapshot = _state(connected=False).snapshot()

    assert snapshot["connected"] is False
    assert snapshot["voice_channel_id"] is None
    assert snapshot["track"] is None


# ---------------------------------------------------------------------------
# Argument coercion: everything here arrives as JSON from a browser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "abc", {}, float("nan"), float("inf")])
def test_a_non_numeric_argument_becomes_a_message_not_a_traceback(value):
    with pytest.raises(VoiceError):
        _coerce_float(value, "volume")


def test_numeric_strings_are_accepted():
    assert _coerce_float("60", "volume") == 60.0


class TestEffects:
    def test_toggles_are_mutually_exclusive_where_they_have_to_be(self):
        state = _state()
        _apply_effects(state, {"nightcore": True})
        assert state._nightcore_enabled is True

        _apply_effects(state, {"vaporwave": True})
        assert state._vaporwave_enabled is True
        assert state._nightcore_enabled is False

    def test_reset_runs_before_the_rest_of_the_payload(self):
        state = _state()
        state._reverb_enabled = True
        state._pitch = 1.5

        _apply_effects(state, {"reset": True, "nightcore": True})

        assert state._reverb_enabled is False
        assert state._pitch == 1.0
        assert state._nightcore_enabled is True

    @pytest.mark.parametrize("payload", [{"pitch": 3.0}, {"pitch": 0.1}, {"bass_boost": 40}])
    def test_out_of_range_values_are_refused(self, payload):
        with pytest.raises(VoiceError):
            _apply_effects(_state(), payload)

    def test_bass_boost_can_be_cleared(self):
        state = _state()
        state._bass_boost = 10
        _apply_effects(state, {"bass_boost": None})
        assert state._bass_boost is None

    def test_absent_keys_are_left_alone(self):
        """A partial payload must not reset the effects it does not mention."""
        state = _state()
        state._reverb_enabled = True
        _apply_effects(state, {"pitch": 1.2})
        assert state._reverb_enabled is True


# ---------------------------------------------------------------------------
# Permissions -- re-derived from the live cache, never trusted from the request
# ---------------------------------------------------------------------------


class TestAuthorize:
    def test_an_unknown_member_is_refused(self):
        cog = _cog()
        with pytest.raises(VoiceError, match="not a member"):
            cog._authorize(_guild(member=None), "42")

    def test_manage_guild_always_passes(self):
        cog = _cog(dj_role_id="777")
        member = _member(manage_guild=True)
        assert cog._authorize(_guild(member), "42") is member

    def test_the_dj_role_is_required_once_one_is_configured(self):
        cog = _cog(dj_role_id="777")
        with pytest.raises(VoiceError, match="DJ role"):
            cog._authorize(_guild(_member(roles=[123])), "42")
        assert cog._authorize(_guild(_member(roles=[777])), "42") is not None

    def test_with_no_dj_role_you_must_be_in_the_bots_channel(self):
        channel = MagicMock()
        state = _state()
        state.voice.channel = channel
        cog = _cog({1: state})

        with pytest.raises(VoiceError, match="Join the voice channel"):
            cog._authorize(_guild(_member(voice_channel=MagicMock())), "42")
        assert cog._authorize(_guild(_member(voice_channel=channel)), "42") is not None

    def test_with_the_bot_idle_you_must_still_be_in_some_channel(self):
        cog = _cog()
        with pytest.raises(VoiceError, match="not connected"):
            cog._authorize(_guild(_member()), "42")
        assert cog._authorize(_guild(_member(voice_channel=MagicMock())), "42") is not None

    def test_a_non_numeric_actor_id_is_refused_rather_than_crashing(self):
        cog = _cog()
        with pytest.raises(VoiceError):
            cog._authorize(_guild(_member()), "not-an-id")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


class TestBridgeActions:
    @pytest.mark.asyncio
    async def test_an_action_on_a_disconnected_guild_is_refused(self):
        cog = _cog()
        with pytest.raises(VoiceError, match="not connected to a voice channel"):
            cog._require_state(1)

    @pytest.mark.asyncio
    async def test_skip_reports_what_it_skipped(self):
        state = _state()
        state.current = Track(title="Doomed", url="http://d")
        cog = _cog({1: state})
        member = _member(manage_guild=True)

        result = await cog._bridge_skip(_guild(member), "42", {})

        assert result == {"skipped": "Doomed"}
        state.voice.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_volume_is_range_checked(self):
        state = _state()
        state.current = Track(title="A", url="http://a")
        cog = _cog({1: state})
        guild = _guild(_member(manage_guild=True))

        assert await cog._bridge_volume(guild, "42", {"volume": 60}) == {"volume": 60}
        assert state.volume == 0.6
        with pytest.raises(VoiceError):
            await cog._bridge_volume(guild, "42", {"volume": 5000})

    @pytest.mark.asyncio
    async def test_loop_only_accepts_the_three_modes(self):
        state = _state()
        cog = _cog({1: state})
        guild = _guild(_member(manage_guild=True))

        assert await cog._bridge_loop(guild, "42", {"mode": "queue"}) == {"loop": "queue"}
        with pytest.raises(VoiceError):
            await cog._bridge_loop(guild, "42", {"mode": "sideways"})

    @pytest.mark.asyncio
    async def test_remove_and_jump_are_bounds_checked(self):
        state = _state()
        state.current = Track(title="Now", url="http://now")
        state.songs.put_nowait(Track(title="A", url="http://a"))
        cog = _cog({1: state})
        guild = _guild(_member(manage_guild=True))

        with pytest.raises(VoiceError, match="not in the queue"):
            await cog._bridge_remove(guild, "42", {"index": 9})
        with pytest.raises(VoiceError, match="not in the queue"):
            await cog._bridge_jump(guild, "42", {"index": -1})
        assert await cog._bridge_remove(guild, "42", {"index": 0}) == {"removed": "A"}

    @pytest.mark.asyncio
    async def test_state_is_readable_without_a_connection_or_a_permission(self):
        """The dashboard must be able to render "nothing is playing"."""
        cog = _cog()
        result = await cog._bridge_state(_guild(member=None), None, {})
        assert result == {"guild_id": "1", "connected": False}

    def test_every_mutating_action_republishes_the_snapshot(self):
        cog = _cog()
        actions = cog.bridge_actions()

        assert set(actions) >= {"player.pause", "player.skip", "player.play", "player.state"}
        # The read-only actions are passed through unwrapped; the rest are not.
        assert actions["player.state"] == cog._bridge_state
        assert actions["player.skip"] != cog._bridge_skip
