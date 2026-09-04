"""Everything Zephyr holds about one person, and how to get rid of it.

Discord requires a privacy policy for app verification, and a policy that
describes a deletion path has to have one. This is that path, and it is also
what `/privacy` reads its retention table from -- so the document and the code
cannot describe different things.

## What is actually held

Written out rather than summarised, because the point of an export is that
somebody can check it is complete:

| Store | Rows keyed on the person | Notes |
|---|---|---|
| `web_users` | `discord_id` | A dashboard login audit. `refresh_token_enc` is always NULL -- this deployment stores no Discord tokens. |
| `bot_users` | `discord_id` | Weather defaults and the AI token budget. |
| `playlists` / `playlist_tracks` | `owner_id` | Saved queues. |
| `audit_log` | `actor_id` | What they changed, per guild. |
| `ai_messages` | `author_id` | Their half of a conversation. |
| `ai_conversations` | -- | Keyed on the *channel*, so shared. Never deleted for one person. |
| Redis sessions | -- | Keyed on the session id. See `SESSION_CAVEAT`. |
| Redis quota counters | `user_id` | Today's token spend. |
| In-process AI buffer | guild / DM | Cleared by the caller; see `zephyr.services.gemini.reset_conversation`. |

## Two things this deliberately does not do

**It does not delete `ai_conversations` rows.** A conversation is keyed on the
channel and holds several people's messages; deleting the row to erase one
person's lines would destroy everybody else's. Their `ai_messages` go, and the
conversation stays.

**It does not delete the audit log's substance.** `audit_log` is a
security-relevant record of who changed a server's settings, and a server owner
has a legitimate interest in it. The `actor_id` is anonymised instead, which
removes the link to the person while leaving the guild's history intact.

## Why children are deleted explicitly

`build_engine` sets no ``PRAGMA foreign_keys=ON``, so every ``ondelete="CASCADE"``
in the schema is **decorative on SQLite** -- which is the development and test
database. Code that trusts a cascade therefore passes CI and behaves differently
on Postgres. Every child row here is deleted by hand.
"""

from __future__ import annotations

# Aliased deliberately. The public function below is also called `delete`, and
# an unaliased import would be shadowed by it -- so `sql_delete(PlaylistTrack)`
# inside it recursed into itself with a table class as the user id, opening a
# connection per level until the pool was exhausted.
from sqlalchemy import delete as sql_delete
from sqlalchemy import select, update

from zephyr.core.logging import get_logger
from zephyr.db.models import (
    AIMessage,
    AuditLog,
    BotUser,
    Playlist,
    PlaylistTrack,
    WebUser,
)
from zephyr.db.session import get_engine

log = get_logger(__name__)

# Stated in the export and in /privacy, because it is a real limitation and a
# policy that glossed over it would be inaccurate.
SESSION_CAVEAT = (
    "Dashboard sessions are stored under a random session id rather than under "
    "your account, so they cannot be listed or revoked individually. Signing out "
    "ends the session you are using, and any other session expires on its own "
    "within 30 days. A session holds your Discord username, avatar and server "
    "list; this deployment stores no Discord access or refresh tokens at all."
)

# The policy text /privacy renders. Kept here so the document and the code that
# implements it cannot drift.
RETENTION = {
    "Dashboard sign-ins": "Your Discord id, username and avatar, kept until you delete them.",
    "Weather defaults": "The city you set with /setlocation, kept until you change or delete it.",
    "Saved playlists": "Titles and links you saved, kept until you delete them.",
    "AI conversations": "Recent messages per channel, so replies have context. Erasable per channel with /forget, or entirely with /delete-my-data.",
    "Server audit log": "Who changed a server's settings. Retained for the server owner; your id is anonymised if you delete your data.",
    "AI usage counters": "Tokens spent today, for rate limiting. Expires automatically within 48 hours.",
    "Sessions": SESSION_CAVEAT,
}

# What an anonymised audit row records instead of the actor's id. A constant, so
# the dashboard can recognise it and render "a removed account" rather than
# showing this string.
ANONYMISED_ACTOR = "deleted-account"


def export(user_id: str, *, database_url: str | None = None) -> dict:
    """Everything held about ``user_id``, as plain JSON-able data."""
    engine = get_engine(database_url)
    user_id = str(user_id)

    with engine.connect() as connection:
        web_user = connection.execute(
            select(
                WebUser.discord_id, WebUser.username, WebUser.global_name,
                WebUser.avatar_hash, WebUser.last_login_at,
            ).where(WebUser.discord_id == user_id)
        ).mappings().first()

        bot_user = connection.execute(
            select(
                BotUser.discord_id, BotUser.default_city, BotUser.lat, BotUser.lon,
                BotUser.units, BotUser.timezone, BotUser.ai_token_budget, BotUser.updated_at,
            ).where(BotUser.discord_id == user_id)
        ).mappings().first()

        playlists = connection.execute(
            select(Playlist.id, Playlist.name, Playlist.guild_id, Playlist.is_public, Playlist.created_at)
            .where(Playlist.owner_id == user_id)
        ).mappings().all()

        tracks_by_playlist: dict[int, list[dict]] = {}
        if playlists:
            track_rows = connection.execute(
                select(PlaylistTrack.playlist_id, PlaylistTrack.position, PlaylistTrack.title, PlaylistTrack.url)
                .where(PlaylistTrack.playlist_id.in_([row["id"] for row in playlists]))
                .order_by(PlaylistTrack.playlist_id, PlaylistTrack.position)
            ).mappings().all()
            for row in track_rows:
                tracks_by_playlist.setdefault(row["playlist_id"], []).append(
                    {"position": row["position"], "title": row["title"], "url": row["url"]}
                )

        audit_rows = connection.execute(
            select(AuditLog.id, AuditLog.guild_id, AuditLog.action, AuditLog.source, AuditLog.created_at)
            .where(AuditLog.actor_id == user_id)
            .order_by(AuditLog.id.desc())
        ).mappings().all()

        messages = connection.execute(
            select(AIMessage.id, AIMessage.conversation_id, AIMessage.role, AIMessage.content, AIMessage.created_at)
            .where(AIMessage.author_id == user_id)
            .order_by(AIMessage.id)
        ).mappings().all()

    return {
        "user_id": user_id,
        "dashboard_account": _serialise(web_user),
        "bot_preferences": _serialise(bot_user),
        "playlists": [
            {**_serialise(row), "tracks": tracks_by_playlist.get(row["id"], [])}
            for row in playlists
        ],
        "audit_entries": [_serialise(row) for row in audit_rows],
        "ai_messages": [_serialise(row) for row in messages],
        "notes": {
            "sessions": SESSION_CAVEAT,
            # Said out loud: an export that silently omitted these would look
            # complete and not be.
            "ai_messages": (
                "Only messages recorded with an author are listed. Messages written "
                "before Zephyr recorded authorship cannot be attributed to anyone and "
                "are not included here or in a deletion."
            ),
            "shared_conversations": (
                "A conversation belongs to a channel and holds several people's "
                "messages, so the conversation itself is not exported or deleted -- "
                "only your own messages within it."
            ),
        },
    }


def delete(user_id: str, *, database_url: str | None = None) -> dict:
    """Erase everything erasable, and report what happened.

    The counts are returned rather than logged, because the person asking is
    entitled to see that something actually happened.
    """
    engine = get_engine(database_url)
    user_id = str(user_id)
    removed: dict[str, int] = {}

    with engine.begin() as connection:
        playlist_ids = [
            row[0] for row in connection.execute(
                select(Playlist.id).where(Playlist.owner_id == user_id)
            ).all()
        ]
        if playlist_ids:
            # Explicitly, not by cascade: SQLite enforces no foreign keys here,
            # so trusting ondelete would orphan every track on the development
            # and test database while working on Postgres.
            removed["playlist_tracks"] = connection.execute(
                sql_delete(PlaylistTrack).where(PlaylistTrack.playlist_id.in_(playlist_ids))
            ).rowcount or 0
            removed["playlists"] = connection.execute(
                sql_delete(Playlist).where(Playlist.id.in_(playlist_ids))
            ).rowcount or 0
        else:
            removed["playlist_tracks"] = 0
            removed["playlists"] = 0

        removed["ai_messages"] = connection.execute(
            sql_delete(AIMessage).where(AIMessage.author_id == user_id)
        ).rowcount or 0

        # Anonymised rather than deleted: this is a security-relevant record of
        # who changed a server's settings, and the server owner has a
        # legitimate interest in keeping it. Removing the link to the person is
        # the part that matters.
        removed["audit_entries_anonymised"] = connection.execute(
            update(AuditLog)
            .where(AuditLog.actor_id == user_id)
            .values(actor_id=ANONYMISED_ACTOR)
        ).rowcount or 0

        removed["bot_preferences"] = connection.execute(
            sql_delete(BotUser).where(BotUser.discord_id == user_id)
        ).rowcount or 0
        removed["dashboard_account"] = connection.execute(
            sql_delete(WebUser).where(WebUser.discord_id == user_id)
        ).rowcount or 0

    return removed


def clear_usage_counters(user_id: str, *, redis_url: str | None = None) -> bool:
    """Forget today's token spend. False when there is no Redis to forget in."""
    if not redis_url:
        return False
    try:
        from zephyr.services import gemini, quota

        _, day = gemini._quota_window()
        quota.clear_user(str(user_id), day, url=redis_url)
        return True
    except Exception:
        # A counter that expires within 48 hours anyway is not worth failing a
        # deletion over.
        log.warning("Could not clear usage counters for %s", user_id, exc_info=True)
        return False


def _serialise(row) -> dict | None:
    if row is None:
        return None
    out = {}
    for key, value in dict(row).items():
        out[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return out
