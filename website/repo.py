"""Database access for the web tier, on SQLAlchemy Core.

Core rather than the ORM, matching the rest of the codebase: there is no
sessionmaker anywhere in Zephyr, and this phase needs one upsert and one select.
An ORM Session would bring a unit of work, an identity map and expire_on_commit
semantics nobody here needs, and doing it correctly under thread-per-request means
scoped_session plus a teardown hook -- more machinery than the queries. The right
time to introduce it is the first phase with relationship traversal.

What is left here is what only the web tier has: ``web_users``, the sign-in audit.
Guild settings moved to ``zephyr/db/guild_settings.py`` once the bot needed to
read ``dj_role_id`` -- a shared table belongs in the shared layer.
"""

from datetime import datetime, timezone

from zephyr.db.models import WebUser
from zephyr.db.session import get_engine


def _insert_for(dialect_name: str):
    """Return the dialect's INSERT construct, which is what carries ON CONFLICT."""
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def upsert_web_user(user: dict, *, database_url: str | None = None) -> None:
    """Record a sign-in.

    ON CONFLICT DO UPDATE rather than UPDATE-then-INSERT-if-zero-rows, which is
    race-prone.  refresh_token_enc is written explicitly as None so the intent is
    visible: Phase 3 stores no Discord tokens at all.
    """
    engine = get_engine(database_url)
    values = {
        "discord_id": str(user["id"]),
        "username": user.get("username") or "",
        "global_name": user.get("global_name"),
        "avatar_hash": user.get("avatar"),
        "refresh_token_enc": None,
        "token_expires_at": None,
        "last_login_at": datetime.now(timezone.utc),
    }
    with engine.begin() as connection:
        insert = _insert_for(connection.dialect.name)
        statement = insert(WebUser.__table__).values(**values)
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["discord_id"],
                set_={
                    "username": statement.excluded.username,
                    "global_name": statement.excluded.global_name,
                    "avatar_hash": statement.excluded.avatar_hash,
                    "last_login_at": statement.excluded.last_login_at,
                },
            )
        )
