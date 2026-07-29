"""Synchronous Discord API client for the web tier.

Mirrors the established pattern in zephyr/utils/weather_utils.py: a thread-local
requests.Session plus a typed exception hierarchy the Flask layer maps onto the
shared error() envelope.  Thread-local matters because gunicorn runs with
--threads 4 and requests.Session is not thread-safe.

Lives under website/ rather than zephyr/utils/ because it is a web-tier concern
and zephyr/ must stay importable by the bot with no web assumptions.
"""

import threading
import time
from typing import Any
from urllib.parse import urlencode

import requests

API_BASE = "https://discord.com/api/v10"
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = f"{API_BASE}/oauth2/token"
CDN_BASE = "https://cdn.discordapp.com"
# Discord requires a User-Agent; omitting it gets the request blocked upstream.
USER_AGENT = "Zephyr-Dashboard (https://github.com/Mist10148/Zephyr-Discord-Bot, 1.0)"

MANAGE_GUILD = 1 << 5
ADMINISTRATOR = 1 << 3

_local = threading.local()


class DiscordError(RuntimeError):
    """Base error for Discord API failures."""


class DiscordTimeoutError(DiscordError):
    """Discord did not answer in time."""


class DiscordUpstreamError(DiscordError):
    """Discord returned an invalid or unsuccessful response."""


class DiscordAuthError(DiscordError):
    """Discord rejected the credentials or the grant."""


class DiscordRateLimited(DiscordError):
    """Discord asked us to back off for longer than we are willing to wait."""

    def __init__(self, retry_after: float):
        super().__init__(f"Rate limited by Discord; retry after {retry_after:.1f}s")
        self.retry_after = retry_after


def _session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def _retry_after(response) -> float:
    header = response.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    try:
        return float(response.json().get("retry_after", 1.0))
    except (ValueError, AttributeError, TypeError):
        return 1.0


def _request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    bot_token: str | None = None,
    data: dict | None = None,
    params: dict | None = None,
    timeout: int = 10,
    _attempt: int = 0,
) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif bot_token:
        headers["Authorization"] = f"Bot {bot_token}"

    url = path if path.startswith("http") else f"{API_BASE}{path}"
    try:
        response = _session().request(
            method, url, headers=headers, data=data, params=params, timeout=timeout
        )
    except requests.Timeout as exc:
        raise DiscordTimeoutError("Discord did not respond in time") from exc
    except requests.RequestException as exc:
        raise DiscordUpstreamError("Discord is unavailable") from exc

    if response.status_code == 429:
        retry_after = _retry_after(response)
        # Never sleep long: two workers x four threads is eight request slots in
        # total, so a 30s sleep would be a self-inflicted outage.
        if retry_after <= 2.0 and _attempt == 0:
            time.sleep(retry_after)
            return _request(
                method,
                path,
                token=token,
                bot_token=bot_token,
                data=data,
                params=params,
                timeout=timeout,
                _attempt=1,
            )
        raise DiscordRateLimited(retry_after)
    if response.status_code in (401, 403):
        raise DiscordAuthError(f"Discord rejected the request ({response.status_code})")
    if response.status_code >= 400:
        raise DiscordUpstreamError(f"Discord returned {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise DiscordUpstreamError("Discord returned a malformed response") from exc


def authorize_url(*, client_id: str, redirect_uri: str, scopes: str, state: str) -> str:
    """Build the consent URL.

    prompt=none skips the consent screen for users who have already authorised,
    which is what makes the silent guilds_stale refresh a redirect round trip with
    no interaction.  Discord still prompts when authorisation is absent or the
    requested scopes changed.
    """
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "scope": scopes,
            "state": state,
            "redirect_uri": redirect_uri,
            "prompt": "none",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def invite_url(*, client_id: str, permissions: str, guild_id: str | None = None) -> str:
    params = {"client_id": client_id, "scope": "bot applications.commands", "permissions": permissions}
    if guild_id:
        params["guild_id"] = guild_id
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str, *, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """Trade an authorisation code for tokens.

    Discord answers with invalid_grant (a 400) when a code is reused, which is why
    a replayed callback surfaces as token_exchange_failed.
    """
    return _request(
        "POST",
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )


def get_current_user(token: str) -> dict:
    return _request("GET", "/users/@me", token=token)


def get_current_user_guilds(token: str) -> list[dict]:
    guilds = _request("GET", "/users/@me/guilds", token=token)
    return guilds if isinstance(guilds, list) else []


def get_bot_guild_ids(bot_token: str) -> set[str]:
    """Every guild the bot is in, as the bot.

    Implemented as a documented fallback for a deployment with no Redis, and
    unused by default: it requires DISCORD_TOKEN in the web service's environment,
    and handing full bot credentials to the internet-facing tier for a cosmetic
    filter is a real increase in blast radius.  Prefer the zephyr:guilds snapshot.
    """
    ids: set[str] = set()
    after = None
    while True:
        params = {"limit": 200}
        if after:
            params["after"] = after
        page = _request("GET", "/users/@me/guilds", bot_token=bot_token, params=params)
        if not isinstance(page, list) or not page:
            return ids
        ids.update(str(guild["id"]) for guild in page)
        if len(page) < 200:
            return ids
        after = page[-1]["id"]


def can_manage(guild: dict) -> bool:
    """Whether the user may administer this guild, per the OAuth guilds payload.

    Owner first: an owner's permissions bitfield does not necessarily contain the
    MANAGE_GUILD bit.  `permissions` arrives as a string.

    This is a UX signal, not authorization.  The bot re-validates permissions
    against its live cache before executing anything.
    """
    if guild.get("owner"):
        return True
    try:
        permissions = int(guild.get("permissions") or 0)
    except (TypeError, ValueError):
        return False
    return bool(permissions & (MANAGE_GUILD | ADMINISTRATOR))


def avatar_url(user: dict, size: int = 128) -> str:
    """The user's avatar, or the default for their account.

    Under the new username system the default index is (id >> 22) % 6.
    """
    avatar = user.get("avatar")
    if avatar:
        return f"{CDN_BASE}/avatars/{user['id']}/{avatar}.png?size={size}"
    try:
        index = (int(user["id"]) >> 22) % 6
    except (KeyError, TypeError, ValueError):
        index = 0
    return f"{CDN_BASE}/embed/avatars/{index}.png"


def guild_icon_url(guild: dict, size: int = 128) -> str | None:
    """The guild's icon, or None -- Discord has no default guild icon."""
    icon = guild.get("icon")
    if not icon:
        return None
    return f"{CDN_BASE}/icons/{guild['id']}/{icon}.png?size={size}"
