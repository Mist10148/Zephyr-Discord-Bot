"""Unit tests for the Discord HTTP client.

requests is patched at the thread-local session seam, so no network calls happen.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from website import discord_api


def _make_response(status=200, json_body=None, headers=None, raise_on_json=False):
    response = MagicMock()
    response.status_code = status
    response.headers = headers or {}
    if raise_on_json:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = json_body if json_body is not None else {}
    return response


class TestRateLimits:
    def test_a_short_retry_after_is_slept_off_and_retried_once(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.side_effect = [
                _make_response(429, headers={"Retry-After": "0.1"}),
                _make_response(200, {"id": "1"}),
            ]
            with patch.object(discord_api.time, "sleep") as sleep:
                assert discord_api.get_current_user("token") == {"id": "1"}
            sleep.assert_called_once_with(0.1)

    def test_a_long_retry_after_raises_without_sleeping(self):
        """Eight request slots total; a 30s sleep would be a self-inflicted outage."""
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.return_value = _make_response(
                429, headers={"Retry-After": "30"}
            )
            with patch.object(discord_api.time, "sleep") as sleep:
                with pytest.raises(discord_api.DiscordRateLimited) as excinfo:
                    discord_api.get_current_user("token")
            sleep.assert_not_called()
        assert excinfo.value.retry_after == 30.0

    def test_two_consecutive_429s_raise_rather_than_looping(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.side_effect = [
                _make_response(429, headers={"Retry-After": "0.1"}),
                _make_response(429, headers={"Retry-After": "0.1"}),
            ]
            with patch.object(discord_api.time, "sleep"):
                with pytest.raises(discord_api.DiscordRateLimited):
                    discord_api.get_current_user("token")

    def test_retry_after_falls_back_to_the_body(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.return_value = _make_response(429, {"retry_after": 9.5})
            with pytest.raises(discord_api.DiscordRateLimited) as excinfo:
                discord_api.get_current_user("token")
        assert excinfo.value.retry_after == 9.5

    def test_an_unparseable_retry_after_still_raises(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.return_value = _make_response(
                429, {}, headers={"Retry-After": "soon"}
            )
            with pytest.raises(discord_api.DiscordRateLimited):
                discord_api.get_current_user("token")


class TestErrorMapping:
    def test_timeout(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.side_effect = requests.Timeout()
            with pytest.raises(discord_api.DiscordTimeoutError):
                discord_api.get_current_user("token")

    def test_connection_error(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.side_effect = requests.ConnectionError()
            with pytest.raises(discord_api.DiscordUpstreamError):
                discord_api.get_current_user("token")

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failures(self, status):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.return_value = _make_response(status)
            with pytest.raises(discord_api.DiscordAuthError):
                discord_api.get_current_user("token")

    @pytest.mark.parametrize("status", [400, 404, 500, 502])
    def test_other_failures(self, status):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.return_value = _make_response(status)
            with pytest.raises(discord_api.DiscordUpstreamError):
                discord_api.get_current_user("token")

    def test_a_malformed_body(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.return_value = _make_response(200, raise_on_json=True)
            with pytest.raises(discord_api.DiscordUpstreamError):
                discord_api.get_current_user("token")

    def test_every_error_is_a_discord_error(self):
        """So callers can catch the base class as a fallback."""
        for error in (
            discord_api.DiscordTimeoutError,
            discord_api.DiscordUpstreamError,
            discord_api.DiscordAuthError,
            discord_api.DiscordRateLimited,
        ):
            assert issubclass(error, discord_api.DiscordError)


class TestRequestShape:
    def test_sends_a_user_agent_and_a_bearer_token(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.return_value = _make_response(200, {"id": "1"})
            discord_api.get_current_user("the-token")
            headers = session.return_value.request.call_args.kwargs["headers"]
        # Discord blocks requests with no User-Agent.
        assert "Zephyr-Dashboard" in headers["User-Agent"]
        assert headers["Authorization"] == "Bearer the-token"

    def test_uses_a_bot_prefix_for_bot_credentials(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.return_value = _make_response(200, [])
            discord_api.get_bot_guild_ids("bot-token")
            headers = session.return_value.request.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bot bot-token"

    def test_exchange_code_posts_form_data(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.return_value = _make_response(200, {"access_token": "at"})
            discord_api.exchange_code(
                "the-code", client_id="cid", client_secret="secret", redirect_uri="http://x/cb"
            )
            call = session.return_value.request.call_args
        assert call.args[0] == "POST"
        assert call.kwargs["data"] == {
            "client_id": "cid",
            "client_secret": "secret",
            "grant_type": "authorization_code",
            "code": "the-code",
            "redirect_uri": "http://x/cb",
        }

    def test_guilds_returns_a_list_even_if_discord_does_not(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.return_value = _make_response(200, {"unexpected": True})
            assert discord_api.get_current_user_guilds("token") == []


class TestBotGuildPagination:
    def test_pages_with_after(self):
        first = [{"id": str(i)} for i in range(200)]
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.side_effect = [
                _make_response(200, first),
                _make_response(200, [{"id": "999"}]),
            ]
            ids = discord_api.get_bot_guild_ids("bot-token")
            second_call = session.return_value.request.call_args_list[1]
        assert "999" in ids
        assert len(ids) == 201
        assert second_call.kwargs["params"]["after"] == "199"

    def test_a_single_short_page_needs_no_second_call(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.return_value = _make_response(200, [{"id": "1"}])
            assert discord_api.get_bot_guild_ids("bot-token") == {"1"}
            assert session.return_value.request.call_count == 1

    def test_no_guilds(self):
        with patch.object(discord_api, "_session") as session:
            session.return_value.request.return_value = _make_response(200, [])
            assert discord_api.get_bot_guild_ids("bot-token") == set()


class TestCanManage:
    def test_owners_always_can(self):
        """An owner's bitfield does not necessarily contain MANAGE_GUILD."""
        assert discord_api.can_manage({"owner": True, "permissions": "0"}) is True

    def test_manage_guild_bit(self):
        assert discord_api.can_manage({"owner": False, "permissions": str(1 << 5)}) is True

    def test_administrator_bit(self):
        assert discord_api.can_manage({"owner": False, "permissions": str(1 << 3)}) is True

    def test_plain_member(self):
        assert discord_api.can_manage({"owner": False, "permissions": "104189505"}) is False

    def test_no_permissions_at_all(self):
        assert discord_api.can_manage({"owner": False, "permissions": "0"}) is False
        assert discord_api.can_manage({}) is False

    def test_permissions_arrive_as_a_string(self):
        guild = {"owner": False, "permissions": "8"}
        assert isinstance(guild["permissions"], str)
        assert discord_api.can_manage(guild) is True

    def test_a_garbage_bitfield_is_not_management(self):
        assert discord_api.can_manage({"owner": False, "permissions": "lots"}) is False


class TestUrls:
    def test_authorize_url(self):
        url = discord_api.authorize_url(
            client_id="cid", redirect_uri="http://x/cb", scopes="identify guilds", state="st"
        )
        assert url.startswith(discord_api.AUTHORIZE_URL)
        assert "scope=identify+guilds" in url
        assert "prompt=none" in url
        assert "state=st" in url

    def test_avatar_url(self):
        url = discord_api.avatar_url({"id": "1", "avatar": "hash"}, size=64)
        assert url == "https://cdn.discordapp.com/avatars/1/hash.png?size=64"

    def test_avatar_url_falls_back_to_the_default_index(self):
        """(id >> 22) % 6 under the new username system."""
        url = discord_api.avatar_url({"id": str(1 << 22), "avatar": None})
        assert url == "https://cdn.discordapp.com/embed/avatars/1.png"

    def test_avatar_url_survives_a_junk_id(self):
        assert discord_api.avatar_url({"id": "abc", "avatar": None}).endswith("/0.png")

    def test_guild_icon_url(self):
        assert discord_api.guild_icon_url({"id": "1", "icon": "h"}, size=128) == (
            "https://cdn.discordapp.com/icons/1/h.png?size=128"
        )

    def test_guild_icon_url_is_none_without_an_icon(self):
        """Discord has no default guild icon, unlike users."""
        assert discord_api.guild_icon_url({"id": "1", "icon": None}) is None

    def test_invite_url_carries_the_client_id_and_permissions(self):
        url = discord_api.invite_url(client_id="cid", permissions="3197952", guild_id="7")
        assert "client_id=cid" in url
        assert "permissions=3197952" in url
        assert "guild_id=7" in url


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
