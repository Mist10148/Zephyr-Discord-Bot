"""The bot <-> web bridge.

Three separate mechanisms, in increasing order of liveness:

* **Membership** (``zephyr:guilds``) -- which servers the bot is in.  No TTL;
  rewritten on every start and on join/leave.
* **Snapshots** (``zephyr:presence``, ``zephyr:player:{guild_id}``) -- short-TTL
  keys the bot writes and the web reads.  Their expiry is the design: an absent
  key means "the bot is not currently telling us", which the dashboard must show
  as offline rather than as stale state presented as current.
* **Commands** (``zephyr:cmd`` -> ``zephyr:res:{id}``) -- the web asks, the bot
  answers, correlated by a per-command id.

The bot remains the authority.  A command carries the actor's Discord id and
**the bot re-validates that actor's live permissions before executing** -- the
web tier's own checks are UX, not security, and are allowed to be stale because
they can only ever over-restrict.

No ``import discord`` -- callers pass plain dicts -- so the web tier can import
this without pulling the gateway library into a Flask worker.  The protocol lives
entirely in this one module rather than being split across a bot half and a web
half, because both sides have to agree on the envelope, the channel names and the
timeout, and two files is how they stop agreeing.
"""

import json
import secrets
import time

# Imported as a module, not `from ... import get_client`, so that patching
# redis_client.get_client redirects every call site at once.
from zephyr.services import redis_client

GUILDS_KEY = "zephyr:guilds"
GUILDS_UPDATED_KEY = "zephyr:guilds:updated_at"

PRESENCE_KEY = "zephyr:presence"
PRESENCE_TTL = 30

# The derived command list. No TTL, deliberately: unlike presence, a command
# list that outlives the process is not a lie -- the commands still exist, and a
# reference that vanished while the bot restarted would be worse than one a few
# minutes stale. See write_guild_snapshot for the same argument.
COMMANDS_KEY = "zephyr:commands"

PLAYER_KEY = "zephyr:player:{guild_id}"
PLAYER_TTL = 60

COMMAND_CHANNEL = "zephyr:cmd"
RESPONSE_CHANNEL = "zephyr:res:{command_id}"
# The plan's number.  Long enough for a voice connect plus a yt-dlp metadata
# fetch, short enough that a gunicorn thread is never held for a visible pause.
COMMAND_TIMEOUT = 5.0


class BridgeError(RuntimeError):
    """The bridge could not complete a command."""


class BridgeTimeout(BridgeError):
    """The bot did not answer in time -- it is down, or busy, or not listening."""


def write_guild_snapshot(guilds: list[dict], *, url: str | None = None) -> None:
    """Replace the published snapshot with ``guilds``.

    Each entry should be ``{"id": str, "name": str, "icon": str | None}``.

    The key has no TTL, unlike a presence key.  Membership changes rarely, and
    expiring it while the bot is briefly down would make every server vanish from
    the picker -- worse than serving a snapshot that is a few minutes stale.  Every
    bot start rewrites it, which bounds the staleness in practice, and
    ``zephyr:guilds:updated_at`` exposes that bound to the UI.
    """
    client = redis_client.get_client(url)
    payload = {str(guild["id"]): guild for guild in guilds}
    pipeline = client.pipeline()
    pipeline.set(GUILDS_KEY, json.dumps(payload))
    pipeline.set(GUILDS_UPDATED_KEY, str(int(time.time())))
    pipeline.execute()


def read_guild_snapshot(*, url: str | None = None) -> tuple[dict[str, dict] | None, int | None]:
    """Return ``(guilds_by_id, updated_at)``, or ``(None, None)`` when unpublished.

    ``None`` means "the bot has never published" -- a distinct state from "the bot
    is in no guilds", and the caller must not present it as an empty list.
    """
    client = redis_client.get_client(url)
    raw, updated = client.mget(GUILDS_KEY, GUILDS_UPDATED_KEY)
    if not raw:
        return None, None
    try:
        guilds = json.loads(raw)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(guilds, dict):
        return None, None
    try:
        stamp = int(updated) if updated else None
    except (TypeError, ValueError):
        stamp = None
    return guilds, stamp


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _read_json(key: str, *, url: str | None = None) -> dict | None:
    """Read one JSON key, treating anything unparseable as absent.

    A snapshot is regenerated within seconds, so a corrupt value is not worth
    raising over -- and reporting it as present-but-broken would give the caller
    a third state to handle for no benefit.
    """
    raw = redis_client.get_client(url).get(key)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_commands(payload: dict, *, url: str | None = None) -> None:
    """Publish the command list derived from the tree.

    Written once at startup rather than on a loop: the set of commands changes
    only when the bot is deployed, and the whole point of deriving it is that
    nothing else has to be told.
    """
    client = redis_client.get_client(url)
    client.set(COMMANDS_KEY, json.dumps({**payload, "published_at": int(time.time())}))


def read_commands(*, url: str | None = None) -> dict | None:
    """The published command list, or None when the bot has never published one."""
    return _read_json(COMMANDS_KEY, url=url)


def write_presence(payload: dict, *, url: str | None = None) -> None:
    """Publish the bot's heartbeat.  Expires, unlike the membership snapshot.

    Membership is better served stale than missing (see ``write_guild_snapshot``),
    but liveness is the exact opposite: a presence key that outlived the process
    that wrote it is a lie, and the dashboard would report a dead bot as online.
    The TTL *is* the liveness signal.
    """
    client = redis_client.get_client(url)
    client.set(PRESENCE_KEY, json.dumps({**payload, "published_at": int(time.time())}), ex=PRESENCE_TTL)


def read_presence(*, url: str | None = None) -> dict | None:
    """The bot's last heartbeat, or None when it has not sent one within the TTL."""
    return _read_json(PRESENCE_KEY, url=url)


def write_player_snapshot(guild_id: str | int, payload: dict, *, url: str | None = None) -> None:
    """Publish one guild's playback state.

    Written on every state change and every few seconds while playing, so the
    60s TTL only expires when the bot stops writing -- which is precisely when
    the dashboard should stop believing it.
    """
    client = redis_client.get_client(url)
    client.set(
        PLAYER_KEY.format(guild_id=guild_id),
        json.dumps({**payload, "published_at": int(time.time())}),
        ex=PLAYER_TTL,
    )


def read_player_snapshot(guild_id: str | int, *, url: str | None = None) -> dict | None:
    """One guild's playback state, or None when nothing is being published."""
    return _read_json(PLAYER_KEY.format(guild_id=guild_id), url=url)


def clear_player_snapshot(guild_id: str | int, *, url: str | None = None) -> None:
    """Drop the snapshot immediately on disconnect.

    Waiting out the TTL would leave the dashboard showing a queue for up to a
    minute after the bot left the channel.
    """
    redis_client.get_client(url).delete(PLAYER_KEY.format(guild_id=guild_id))


# ---------------------------------------------------------------------------
# Commands: web -> bot -> web
# ---------------------------------------------------------------------------


def send_command(
    action: str,
    *,
    guild_id: str | int | None = None,
    actor_id: str | int | None = None,
    args: dict | None = None,
    timeout: float = COMMAND_TIMEOUT,
    url: str | None = None,
) -> dict:
    """Publish a command and block until the bot answers.

    **Subscribes to the response channel before publishing the command.**  The
    other order is a race the bot wins on any fast action: it would reply before
    the subscription existed, the reply would be dropped by Redis (pub/sub has no
    backlog), and the caller would then wait the full timeout for an answer that
    had already been sent.

    Returns the response envelope's ``data``.  Raises ``BridgeTimeout`` when
    nothing answers, and ``BridgeError`` when the bot answers with a refusal --
    which includes every permission rejection, since the bot is the authority on
    those.
    """
    command_id = secrets.token_urlsafe(16)
    client = redis_client.get_client(url)
    channel = RESPONSE_CHANNEL.format(command_id=command_id)

    pubsub = client.pubsub(ignore_subscribe_messages=True)
    try:
        pubsub.subscribe(channel)
        envelope = {
            "id": command_id,
            "guild_id": str(guild_id) if guild_id is not None else None,
            "actor_id": str(actor_id) if actor_id is not None else None,
            "action": action,
            "args": args or {},
            "issued_at": int(time.time()),
        }
        client.publish(COMMAND_CHANNEL, json.dumps(envelope))

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeTimeout("Zephyr did not respond. It may be offline.")
            message = pubsub.get_message(timeout=min(remaining, 1.0))
            if not message or message.get("type") != "message":
                continue
            response = _decode(message.get("data"))
            if response is None:
                # Somebody published junk on our private channel; keep waiting
                # rather than reporting a malformed reply as the bot's answer.
                continue
            if not response.get("ok"):
                raise BridgeError(response.get("error") or "Zephyr refused that request.")
            return response.get("data") or {}
    finally:
        try:
            pubsub.close()
        except Exception:
            pass


def open_command_stream(*, url: str | None = None):
    """Bot side: subscribe to the command channel.  Returns a redis PubSub."""
    pubsub = redis_client.get_client(url).pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(COMMAND_CHANNEL)
    return pubsub


def next_command(pubsub, *, timeout: float = 0.5) -> dict | None:
    """Bot side: the next well-formed command, or None.

    Malformed envelopes are dropped rather than raised: the channel is shared,
    anything may be published on it, and one bad message must not stop the
    listener.  A command with no id is unanswerable, so it is not worth
    dispatching either.
    """
    message = pubsub.get_message(timeout=timeout)
    if not message or message.get("type") != "message":
        return None
    command = _decode(message.get("data"))
    if command is None:
        return None
    if not isinstance(command.get("id"), str) or not isinstance(command.get("action"), str):
        return None
    if not isinstance(command.get("args"), dict):
        command["args"] = {}
    return command


def publish_response(
    command_id: str,
    *,
    ok: bool,
    data: dict | None = None,
    error: str | None = None,
    url: str | None = None,
) -> None:
    """Bot side: answer one command."""
    payload = {"ok": bool(ok)}
    if data is not None:
        payload["data"] = data
    if error is not None:
        payload["error"] = error
    redis_client.get_client(url).publish(
        RESPONSE_CHANNEL.format(command_id=command_id), json.dumps(payload)
    )


def _decode(raw) -> dict | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None
