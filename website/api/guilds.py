"""Per-guild endpoints: the overview, editable settings, and Discord metadata.

``GET /guilds/<id>`` is the read-only overview Phase 3 shipped.  Phase 4 adds the
settings resource the plan asked for at ``/guilds/:id/settings`` -- a separate
path, which is why the overview was never put there.
"""

from flask import current_app, g, jsonify, request

from website import discord_api
from website.api import api, error
from website.api.guard import guild_scoped
from zephyr.config import COMMAND_PREFIX
from zephyr.db import audit
from zephyr.db.guild_settings import read_guild_settings, write_guild_settings
from zephyr.services import bridge
from zephyr.core.logging import get_logger


log = get_logger(__name__)
# The audit writers only ever set these two, so an unknown value is a client bug
# rather than something to pass through to a WHERE.
AUDIT_SOURCES = {"web", "discord"}

# Applied when a guild has no row yet, which is the normal state until somebody
# saves settings.  Reported with defaults_applied so the UI can say so.
DEFAULT_SETTINGS = {
    # Was "/", which is the value the bot stopped using precisely because every
    # message beginning with a slash was then also parsed as a prefix command.
    # Read from config so the dashboard and the bot cannot disagree about what
    # "unconfigured" means.
    "prefix": COMMAND_PREFIX,
    "locale": "en",
    "timezone": "UTC",
    "default_volume": 50,
    "dj_role_id": None,
    "music_channel_ids": [],
    # gTTS language code for /say. "en" matches what the cog used to hardcode.
    "tts_language": "en",
    # Where the AI answers a mention. None means everywhere it can read, which
    # is the historical behaviour and stays the default.
    "ai_channel_mode": None,
    "ai_channel_ids": [],
    # Where moderation cases are posted. None means the modlog is off; cases
    # are still recorded and readable with /case.
    "modlog_channel_id": None,
}


def _settings_payload(guild_id: str) -> tuple[dict, bool]:
    stored = read_guild_settings(guild_id, database_url=current_app.config["DATABASE_URL"])
    settings = dict(DEFAULT_SETTINGS)
    settings["enabled_cogs"] = list(current_app.config["ENABLED_COGS"])
    if stored:
        for key, value in stored.items():
            if key != "id" and value is not None:
                settings[key] = value
    return settings, stored is None


@api.get("/guilds/<guild_id>")
@guild_scoped
def guild_overview(guild_id: str):
    guild = g.zephyr_guild
    try:
        snapshot, snapshot_at = bridge.read_guild_snapshot(url=current_app.config["REDIS_URL"])
    except Exception as exc:
        log.exception("Could not read the guild snapshot")
        snapshot, snapshot_at = None, None
    bot_present = None if snapshot is None else guild_id in snapshot

    settings, defaults_applied = _settings_payload(guild_id)
    return jsonify(
        {
            "id": guild_id,
            "name": guild.get("name") or "",
            "icon": guild.get("icon"),
            "icon_url": discord_api.guild_icon_url(guild),
            "owner": bool(guild.get("owner")),
            "bot_present": bot_present,
            "bot_snapshot_at": snapshot_at,
            "defaults_applied": defaults_applied,
            "editable": True,
            **settings,
        }
    )


@api.get("/guilds/<guild_id>/settings")
@guild_scoped
def get_guild_settings(guild_id: str):
    settings, defaults_applied = _settings_payload(guild_id)
    return jsonify({"id": guild_id, "defaults_applied": defaults_applied, **settings})


# Every field the dashboard may write, with the check that makes it safe.  An
# explicit table rather than "whatever keys arrived": a PATCH body is user input,
# and enabled_cogs in particular is derived from deployment configuration and
# must not become writable just because it is returned by the GET.
def _clean_prefix(value):
    text = str(value).strip()
    if not 1 <= len(text) <= 5:
        raise ValueError("A prefix must be 1 to 5 characters.")
    return text


def _clean_locale(value):
    text = str(value).strip()
    if not 2 <= len(text) <= 10:
        raise ValueError("That is not a locale.")
    return text


def _clean_timezone(value):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    text = str(value).strip()
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, ModuleNotFoundError):
        raise ValueError("That is not an IANA timezone name.") from None
    return text


def _clean_volume(value):
    try:
        volume = int(value)
    except (TypeError, ValueError):
        raise ValueError("Default volume must be a number.") from None
    if not 0 <= volume <= 200:
        raise ValueError("Default volume must be between 0 and 200.")
    return volume


def _clean_snowflake(value):
    if value in (None, ""):
        return None
    text = str(value)
    if not text.isdigit():
        raise ValueError("That is not a Discord id.")
    return text


def _clean_tts_language(value):
    """A gTTS language code, checked against the list gTTS actually supports.

    Validated here as well as in the command, because the dashboard is the
    other way in and a bad code fails at speech time with "TTS failed: ...",
    which points at the wrong thing entirely.
    """
    text = str(value).strip().lower()
    if not text:
        raise ValueError("A language code is required.")
    from zephyr.cogs.voice_tts import supported_languages

    if text not in supported_languages():
        raise ValueError(f"{text!r} is not a language gTTS can speak.")
    return text


def _clean_ai_channel_mode(value):
    """None, "allow" or "deny". None means everywhere the bot can read."""
    if value in (None, "", "all"):
        return None
    text = str(value).strip().lower()
    if text not in {"allow", "deny"}:
        raise ValueError('ai_channel_mode must be "allow", "deny" or null.')
    return text


def _clean_snowflake_list(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("That must be a list of channel ids.")
    if len(value) > 25:
        raise ValueError("At most 25 channels.")
    return [_clean_snowflake(item) for item in value if _clean_snowflake(item)]


CLEANERS = {
    "prefix": _clean_prefix,
    "locale": _clean_locale,
    "timezone": _clean_timezone,
    "default_volume": _clean_volume,
    "tts_language": _clean_tts_language,
    "ai_channel_mode": _clean_ai_channel_mode,
    "ai_channel_ids": _clean_snowflake_list,
    "dj_role_id": _clean_snowflake,
    "music_channel_ids": _clean_snowflake_list,
    "modlog_channel_id": _clean_snowflake,
}


@api.patch("/guilds/<guild_id>/settings")
@guild_scoped
def patch_guild_settings(guild_id: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error("invalid_body", "Send a JSON object.", 400)

    unknown = sorted(set(body) - set(CLEANERS))
    if unknown:
        # Rejected rather than ignored: silently dropping a field the caller
        # believes it saved is the worse failure of the two.
        return error("unknown_fields", f"Cannot set: {', '.join(unknown)}.", 400, unknown)

    values = {}
    for key, value in body.items():
        try:
            values[key] = CLEANERS[key](value)
        except ValueError as exc:
            return error("invalid_value", str(exc), 400, {"field": key})
    if not values:
        return error("invalid_body", "Nothing to change.", 400)

    stored = write_guild_settings(guild_id, values, database_url=current_app.config["DATABASE_URL"])
    audit.record(
        "settings.update",
        actor_id=g.zephyr_session.user_id,
        guild_id=guild_id,
        payload=values,
        source="web",
        database_url=current_app.config["DATABASE_URL"],
    )

    # The bot caches dj_role_id for its permission check, so a save that did not
    # reach it would leave the DJ role wrong until the next slow refresh.  Best
    # effort: the setting is saved either way, and the cache self-corrects.
    if "dj_role_id" in values:
        try:
            bridge.send_command("settings.reload", url=current_app.config["REDIS_URL"], timeout=2.0)
        except Exception as exc:
            log.warning("Could not tell the bot to reload settings: %s", exc)

    settings = dict(DEFAULT_SETTINGS)
    settings["enabled_cogs"] = list(current_app.config["ENABLED_COGS"])
    for key, value in (stored or {}).items():
        if key != "id" and value is not None:
            settings[key] = value
    return jsonify({"id": guild_id, "defaults_applied": False, **settings})


@api.get("/guilds/<guild_id>/audit")
@guild_scoped
def guild_audit(guild_id: str):
    """One page of the guild's audit trail, newest first.

    The writers have existed since Phase 4 (``settings.update`` here, every
    ``player.*`` and ``ai.*`` action elsewhere); this is the reader the plan
    deferred to Phase 7.  Pagination is keyset -- ``?before=<id>`` -- so a busy
    guild writing rows between page loads cannot make the cursor skip or repeat
    an entry the way an offset would.
    """
    def _int_arg(name):
        raw = request.args.get(name)
        if raw is None or raw == "":
            return None
        if not raw.isdigit():
            raise ValueError(name)
        return int(raw)

    try:
        limit = _int_arg("limit")
        before = _int_arg("before")
    except ValueError as exc:
        return error("invalid_query", f"{exc.args[0]} must be a positive integer.", 400)

    # Allow-listed rather than free text. `action` reaches a LIKE prefix, and
    # `source` and `actor_id` reach equality; bounding the length and shape keeps
    # a caller from turning the endpoint into a table scan with a 4KB pattern.
    action = (request.args.get("action") or "").strip()[:64] or None
    actor = (request.args.get("actor_id") or "").strip()[:32] or None
    source = (request.args.get("source") or "").strip()[:16] or None
    if actor is not None and not actor.isdigit():
        return error("invalid_query", "actor_id must be a Discord id.", 400)
    if source is not None and source not in AUDIT_SOURCES:
        return error("invalid_query", f"source must be one of {', '.join(sorted(AUDIT_SOURCES))}.", 400)

    kwargs = {
        "database_url": current_app.config["DATABASE_URL"],
        "before_id": before,
        "action": action,
        "actor_id": actor,
        "source": source,
    }
    if limit is not None:
        kwargs["limit"] = limit
    try:
        page = audit.read(guild_id, **kwargs)
    except Exception as exc:
        log.exception("Could not read the audit log for %s", guild_id)
        return error("audit_unavailable", "Could not read the audit log.", 503)
    return jsonify({"id": guild_id, **page, "actors": _actor_names(guild_id, page.get("entries") or [])})


def _actor_names(guild_id: str, entries: list[dict]) -> dict:
    """Display names for the actors on this page, keyed by id.

    The log stores an ``actor_id`` and nothing else, so every row read "Changed
    by 403285930202595340". The web tier has no gateway connection and stores no
    Discord token, so the bot is the only thing that can put a name to an id.

    Asked once per page for the distinct set rather than per row: a page of
    fifty entries is usually two or three people. Failure is not an error --
    an unreachable bot returns ``{}`` and the client falls back to the raw id,
    exactly as the settings pickers already degrade. An audit log that 503s
    because a name lookup failed would be a strictly worse page.
    """
    ids = sorted({str(entry["actor_id"]) for entry in entries if entry.get("actor_id")})
    if not ids:
        return {}
    redis_url = current_app.config["REDIS_URL"]
    if not redis_url:
        return {}
    try:
        answer = bridge.send_command(
            "meta.members", guild_id=guild_id, args={"ids": ids}, url=redis_url
        )
    except Exception as exc:
        log.warning("Could not resolve audit actors for %s: %s", guild_id, exc)
        return {}
    return {member["id"]: member for member in answer.get("members") or [] if member.get("id")}


@api.get("/guilds/<guild_id>/meta")
@guild_scoped
def guild_meta(guild_id: str):
    """Channels and roles, asked of the bot in real time.

    The web tier has no gateway connection and stores no Discord token, so this
    is the only way it can put a name next to a channel id.  Answered over the
    bridge rather than from a Redis snapshot: it is read once when a settings
    page opens, so a round trip is cheaper than keeping a mirror of every
    guild's channel list continuously up to date.
    """
    from website.api.player import bridge_call

    return bridge_call("meta.guild", guild_id=guild_id)
