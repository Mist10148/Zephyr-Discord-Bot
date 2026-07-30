"""Snapshots and the command channel.

Nothing here touches a real Redis: ``FakeRedis`` implements pub/sub in memory,
with a ``get_message`` that genuinely blocks for its timeout, so the wait loop is
exercised rather than mocked out.
"""

import json
import time

import pytest

from zephyr.services import bridge


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def test_presence_round_trips_and_expires(fake_redis):
    bridge.write_presence({"online": True, "guild_count": 3, "latency_ms": 42})

    presence = bridge.read_presence()
    assert presence["online"] is True
    assert presence["guild_count"] == 3
    assert "published_at" in presence

    # The TTL is the liveness signal: an expired key must read as "no heartbeat",
    # never as the last one it happened to hold.
    assert 0 < fake_redis.ttl_of(bridge.PRESENCE_KEY) <= bridge.PRESENCE_TTL
    fake_redis.expire_now(bridge.PRESENCE_KEY)
    assert bridge.read_presence() is None


def test_player_snapshots_are_per_guild_and_clearable(fake_redis):
    bridge.write_player_snapshot("1", {"paused": False, "queue": [{"title": "A"}]})
    bridge.write_player_snapshot("2", {"paused": True, "queue": []})

    assert bridge.read_player_snapshot("1")["queue"][0]["title"] == "A"
    assert bridge.read_player_snapshot("2")["paused"] is True
    assert bridge.read_player_snapshot("3") is None
    assert 0 < fake_redis.ttl_of(bridge.PLAYER_KEY.format(guild_id="1")) <= bridge.PLAYER_TTL

    # Disconnect must not leave a queue on screen for the rest of the TTL.
    bridge.clear_player_snapshot("1")
    assert bridge.read_player_snapshot("1") is None


def test_a_corrupt_snapshot_reads_as_absent(fake_redis):
    fake_redis.set(bridge.PRESENCE_KEY, "{not json")
    assert bridge.read_presence() is None
    fake_redis.set(bridge.PLAYER_KEY.format(guild_id="1"), '["a list"]')
    assert bridge.read_player_snapshot("1") is None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _reply_from_the_command(fake_redis, *, ok=True, data=None, error=None):
    """Answer on the response channel the moment the command is published.

    This is the strict case: pub/sub keeps no backlog, so a reply sent this early
    is lost unless send_command subscribed before publishing.
    """

    def responder(channel, raw):
        if channel != bridge.COMMAND_CHANNEL:
            return
        command = json.loads(raw)
        bridge.publish_response(command["id"], ok=ok, data=data, error=error)

    fake_redis.on_publish = responder


def test_send_command_returns_the_bots_data(fake_redis):
    _reply_from_the_command(fake_redis, data={"skipped": "A Song"})

    result = bridge.send_command("player.skip", guild_id="1", actor_id="42")

    assert result == {"skipped": "A Song"}


def test_the_response_subscription_exists_before_the_command_is_published(fake_redis):
    """The ordering, asserted directly rather than inferred from the happy path."""
    subscribed_channels = []
    fake_redis.on_publish = lambda channel, raw: subscribed_channels.append(
        [set(sub.channels) for sub in fake_redis.subscribers]
    )

    with pytest.raises(bridge.BridgeTimeout):
        bridge.send_command("player.skip", guild_id="1", actor_id="42", timeout=0.05)

    # At publish time exactly one subscriber existed, and it was already on this
    # command's private response channel.
    assert any(
        channel.startswith("zephyr:res:") for channels in subscribed_channels[0] for channel in channels
    )


def test_the_envelope_carries_the_actor_the_bot_must_re_validate(fake_redis):
    seen = {}

    def responder(channel, raw):
        if channel == bridge.COMMAND_CHANNEL:
            seen.update(json.loads(raw))
            bridge.publish_response(seen["id"], ok=True, data={})

    fake_redis.on_publish = responder
    bridge.send_command("player.volume", guild_id="1", actor_id="42", args={"volume": 60})

    assert seen["guild_id"] == "1"
    assert seen["actor_id"] == "42"
    assert seen["action"] == "player.volume"
    assert seen["args"] == {"volume": 60}
    assert isinstance(seen["issued_at"], int)


def test_a_refusal_is_an_error_not_a_result(fake_redis):
    """Permission rejections come back this way -- the bot is the authority."""
    _reply_from_the_command(fake_redis, ok=False, error="You are not in the voice channel.")

    with pytest.raises(bridge.BridgeError, match="not in the voice channel"):
        bridge.send_command("player.skip", guild_id="1", actor_id="42")


def test_no_answer_times_out_rather_than_hanging(fake_redis):
    started = time.monotonic()
    with pytest.raises(bridge.BridgeTimeout):
        bridge.send_command("player.skip", guild_id="1", actor_id="42", timeout=0.1)
    elapsed = time.monotonic() - started
    assert 0.05 <= elapsed < 2


def test_junk_on_the_response_channel_does_not_become_the_answer(fake_redis):
    def responder(channel, raw):
        if channel != bridge.COMMAND_CHANNEL:
            return
        command_id = json.loads(raw)["id"]
        channel_name = bridge.RESPONSE_CHANNEL.format(command_id=command_id)
        fake_redis.publish(channel_name, "not json at all")
        bridge.publish_response(command_id, ok=True, data={"ok": "eventually"})

    fake_redis.on_publish = responder
    assert bridge.send_command("player.skip", guild_id="1") == {"ok": "eventually"}


def test_the_subscription_is_closed_even_when_the_command_fails(fake_redis):
    with pytest.raises(bridge.BridgeTimeout):
        bridge.send_command("player.skip", guild_id="1", timeout=0.05)
    assert fake_redis.subscribers == []


# ---------------------------------------------------------------------------
# The bot's side of the channel
# ---------------------------------------------------------------------------


def test_the_bot_reads_well_formed_commands_and_drops_the_rest(fake_redis):
    stream = bridge.open_command_stream()

    fake_redis.publish(bridge.COMMAND_CHANNEL, "{{{")
    fake_redis.publish(bridge.COMMAND_CHANNEL, json.dumps(["not", "a", "dict"]))
    fake_redis.publish(bridge.COMMAND_CHANNEL, json.dumps({"action": "player.skip"}))  # no id
    fake_redis.publish(bridge.COMMAND_CHANNEL, json.dumps({"id": "abc"}))  # no action
    fake_redis.publish(bridge.COMMAND_CHANNEL, json.dumps({"id": "x", "action": "player.skip"}))

    # Four drops, then the real one -- and a drop must never stop the listener.
    received = [bridge.next_command(stream, timeout=0) for _ in range(5)]
    assert [command for command in received if command] == [
        {"id": "x", "action": "player.skip", "args": {}}
    ]


def test_next_command_returns_none_when_nothing_is_waiting(fake_redis):
    assert bridge.next_command(bridge.open_command_stream(), timeout=0) is None


# ---------------------------------------------------------------------------
# Dispatch: the two halves meeting
# ---------------------------------------------------------------------------


def _bot(actions):
    """A ZephyrBot with no gateway -- __init__ would build a real client."""
    from zephyr.client import ZephyrBot

    bot = ZephyrBot.__new__(ZephyrBot)

    class StubCog:
        def bridge_actions(self):
            return actions

    # Bot.cogs is a read-only MappingProxy over BotBase's name-mangled dict, and
    # add_cog() would need a real Cog subclass plus a running loop.  Writing the
    # backing attribute keeps this a test of _bridge_actions' discovery rather
    # than of discord.py's cog machinery.
    bot._BotBase__cogs = {"Stub": StubCog()}
    bot.get_guild = lambda guild_id: f"guild:{guild_id}"
    return bot


def test_a_command_reaches_a_cog_handler_and_the_answer_comes_back(fake_redis):
    seen = {}

    async def handler(guild, actor_id, args):
        seen.update({"guild": guild, "actor_id": actor_id, "args": args})
        return {"skipped": "A Song"}

    bot = _bot({"player.skip": handler})
    fake_redis.on_publish = lambda channel, raw: (
        None if channel != bridge.COMMAND_CHANNEL else _run_soon(bot, json.loads(raw))
    )

    result = bridge.send_command("player.skip", guild_id="1", actor_id="42", args={"n": 1})

    assert result == {"skipped": "A Song"}
    assert seen == {"guild": "guild:1", "actor_id": "42", "args": {"n": 1}}


def test_an_unknown_action_is_answered_rather_than_left_to_time_out(fake_redis):
    """Silence would be diagnosed as "the bot is offline", which is much worse."""
    bot = _bot({})
    fake_redis.on_publish = lambda channel, raw: (
        None if channel != bridge.COMMAND_CHANNEL else _run_soon(bot, json.loads(raw))
    )

    with pytest.raises(bridge.BridgeError, match="Unknown action"):
        bridge.send_command("player.teleport", guild_id="1", actor_id="42")


def test_a_handler_that_raises_answers_with_its_message(fake_redis):
    async def handler(guild, actor_id, args):
        raise RuntimeError("Join the voice channel Zephyr is in.")

    bot = _bot({"player.skip": handler})
    fake_redis.on_publish = lambda channel, raw: (
        None if channel != bridge.COMMAND_CHANNEL else _run_soon(bot, json.loads(raw))
    )

    with pytest.raises(bridge.BridgeError, match="Join the voice channel"):
        bridge.send_command("player.skip", guild_id="1", actor_id="42")


def _run_soon(bot, command):
    """Dispatch synchronously from inside publish().

    ``send_command`` is blocking, so there is no running loop to schedule onto --
    and dispatching here is also the strictest possible test of the
    subscribe-before-publish ordering, since the reply is produced before
    ``publish`` has even returned.
    """
    import asyncio

    asyncio.run(bot._dispatch_command(command))
