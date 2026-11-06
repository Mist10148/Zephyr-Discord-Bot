"""Guild mutations re-check Manage Server with the bot, and fail closed.

The session's guild list is captured at sign-in and lives for weeks, so someone
who lost Manage Server could otherwise keep changing that server's settings,
weather subscriptions and AI data from the dashboard.
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from website.api import guard
from zephyr.client import ZephyrBot
from zephyr.services import bridge

# Captured at import, before conftest's autouse stub replaces it per test.
REAL_CONFIRM = guard.confirm_still_admin


@pytest.fixture(autouse=True)
def real_check(monkeypatch):
    monkeypatch.setattr(guard, "confirm_still_admin", REAL_CONFIRM)


def _headers(logged_in):
    return {"X-Zephyr-CSRF": logged_in.csrf}


def _bot(fake_redis, *, ok=True, error=None):
    """Stand in for the bot; returns the actions it was asked to run."""
    seen = []

    def responder(channel, raw):
        if channel != bridge.COMMAND_CHANNEL:
            return
        command = json.loads(raw)
        seen.append(command["action"])
        bridge.publish_response(command["id"], ok=ok, data={}, error=error)

    fake_redis.on_publish = responder
    return seen


def _patch_settings(client, logged_in):
    return client.patch("/api/v1/guilds/1/settings", json={"prefix": "!"}, headers=_headers(logged_in))


class TestWebTier:
    def test_a_current_manager_can_change_settings(self, client, logged_in, fake_redis):
        seen = _bot(fake_redis)

        assert _patch_settings(client, logged_in).status_code == 200
        assert seen[0] == "auth.manage_guild"

    def test_someone_who_lost_manage_server_is_refused(self, client, logged_in, fake_redis):
        _bot(fake_redis, ok=False, error="You no longer manage that server.")

        response = _patch_settings(client, logged_in)

        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "forbidden"
        assert client.get("/api/v1/guilds/1/settings").get_json()["prefix"] != "!"

    def test_an_offline_bot_refuses_the_change(self, client, logged_in, fake_redis, monkeypatch):
        """Fail closed: without the bot there is no current answer."""
        real_send = bridge.send_command
        monkeypatch.setattr(bridge, "send_command", lambda *a, **k: real_send(*a, **{**k, "timeout": 0.05}))

        response = _patch_settings(client, logged_in)

        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "bot_unreachable"

    def test_reads_do_not_ask_the_bot(self, client, logged_in, fake_redis):
        seen = _bot(fake_redis)

        assert client.get("/api/v1/guilds/1/settings").status_code == 200
        assert "auth.manage_guild" not in seen

    def test_player_actions_leave_the_check_to_the_bot(self, client, logged_in, fake_redis):
        seen = _bot(fake_redis)

        client.post("/api/v1/guilds/1/player/skip", headers=_headers(logged_in))

        assert seen == ["player.skip"]


def _member(manage_guild):
    member = MagicMock()
    member.guild_permissions.manage_guild = manage_guild
    return member


def _confirm(guild, actor_id="42"):
    bot = ZephyrBot.__new__(ZephyrBot)
    return asyncio.run(bot._bridge_confirm_manager(guild, actor_id, {}))


class TestBotSide:
    def test_a_manager_is_confirmed(self):
        guild = MagicMock()
        guild.get_member.return_value = _member(True)

        assert _confirm(guild) == {"manage_guild": True}
        guild.get_member.assert_called_once_with(42)

    def test_a_member_without_manage_server_is_refused(self):
        guild = MagicMock()
        guild.get_member.return_value = _member(False)

        with pytest.raises(PermissionError):
            _confirm(guild)

    def test_someone_no_longer_in_the_server_is_refused(self):
        guild = MagicMock()
        guild.get_member.return_value = None

        with pytest.raises(PermissionError):
            _confirm(guild)

    def test_a_malformed_actor_is_refused(self):
        with pytest.raises(PermissionError):
            _confirm(MagicMock(), actor_id=None)

    def test_a_guild_the_bot_left_is_an_error(self):
        with pytest.raises(LookupError):
            _confirm(None)
