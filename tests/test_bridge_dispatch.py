"""The bot's side of the bridge: concurrent dispatch and expired commands."""

import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest

from zephyr import client as client_module
from zephyr.client import ZephyrBot
from zephyr.services import bridge


def _bot(actions):
    bot = ZephyrBot.__new__(ZephyrBot)
    bot._command_tasks = set()
    bot._bridge_actions = lambda: actions
    bot.get_guild = MagicMock(return_value=None)
    return bot


def test_the_envelope_says_when_the_caller_stops_listening(fake_redis):
    seen = {}

    def responder(channel, raw):
        if channel == bridge.COMMAND_CHANNEL:
            seen.update(json.loads(raw))
            bridge.publish_response(seen["id"], ok=True, data={})

    fake_redis.on_publish = responder
    before = time.time()
    bridge.send_command("player.skip", guild_id="1", timeout=7)

    assert before + 7 <= seen["expires_at"] <= time.time() + 7


@pytest.mark.asyncio
async def test_an_expired_command_is_dropped_not_run(monkeypatch):
    ran = []

    async def skip(guild, actor_id, args):
        ran.append(args)

    published = []
    monkeypatch.setattr(bridge, "publish_response", lambda *a, **k: published.append((a, k)))
    bot = _bot({"player.skip": skip})

    await bot._dispatch_command({"id": "x", "action": "player.skip", "args": {},
                                 "expires_at": time.time() - client_module.COMMAND_EXPIRY_GRACE_SECONDS - 5})

    assert ran == [] and published == []


@pytest.mark.asyncio
async def test_a_live_command_still_runs(monkeypatch):
    ran = []

    async def skip(guild, actor_id, args):
        ran.append(True)
        return {"ok": 1}

    monkeypatch.setattr(bridge, "publish_response", lambda *a, **k: None)
    bot = _bot({"player.skip": skip})
    await bot._dispatch_command({"id": "x", "action": "player.skip", "args": {}, "expires_at": time.time() + 5})
    assert ran == [True]


@pytest.mark.asyncio
async def test_a_slow_command_does_not_hold_up_the_next_one(monkeypatch):
    release = asyncio.Event()
    order = []

    async def slow(guild, actor_id, args):
        await release.wait()
        order.append("slow")

    async def fast(guild, actor_id, args):
        order.append("fast")
        release.set()

    monkeypatch.setattr(bridge, "publish_response", lambda *a, **k: None)
    bot = _bot({"player.play": slow, "player.skip": fast})
    batch = [{"id": "1", "action": "player.play", "args": {}}, {"id": "2", "action": "player.skip", "args": {}}]
    monkeypatch.setattr(ZephyrBot, "_read_commands", lambda self: batch)

    await asyncio.wait_for(ZephyrBot._command_loop.coro(bot), timeout=1)
    await asyncio.wait_for(asyncio.gather(*bot._command_tasks), timeout=1)

    assert order == ["fast", "slow"]


@pytest.mark.asyncio
async def test_a_handler_can_see_when_its_caller_gives_up(monkeypatch):
    seen = []

    async def play(guild, actor_id, args):
        seen.append(bridge.command_deadline.get())

    monkeypatch.setattr(bridge, "publish_response", lambda *a, **k: None)
    bot = _bot({"player.play": play})
    expires = time.time() + 20
    await asyncio.create_task(bot._dispatch_command(
        {"id": "x", "action": "player.play", "args": {}, "expires_at": expires}))

    assert seen == [expires + client_module.COMMAND_EXPIRY_GRACE_SECONDS]
