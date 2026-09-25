"""Persistence for AI conversations, history management, and guild personas."""

from sqlalchemy import delete, desc, exists, func, insert, select, update

from zephyr.db.models import AIConversation, AIMessage, AIMessageRevision, Persona
from zephyr.db.session import get_engine

MAX_PERSONA_NAME = 64
MAX_PERSONA_PROMPT = 4000
MAX_HISTORY_QUERY = 100
MAX_HISTORY_LIMIT = 50
MAX_MESSAGE_CONTENT = 10000
MAX_EDIT_REASON = 500
REDACTED_CONTENT = "[Message redacted by a server administrator.]"


class AIDataError(ValueError):
    pass


class AIConflictError(AIDataError):
    """Raised when an edit is based on an older message version."""


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _validate_history_query(query):
    query = " ".join(str(query or "").split())
    if len(query) > MAX_HISTORY_QUERY:
        raise AIDataError(f"Search terms must be {MAX_HISTORY_QUERY} characters or fewer.")
    return query


def _validate_message_content(content):
    content = str(content or "").strip()
    if not content:
        raise AIDataError("Message content cannot be empty.")
    if len(content) > MAX_MESSAGE_CONTENT:
        raise AIDataError(f"Message content must be {MAX_MESSAGE_CONTENT} characters or fewer.")
    return content


def _validate_reason(reason):
    reason = " ".join(str(reason or "").split())
    if len(reason) > MAX_EDIT_REASON:
        raise AIDataError(f"Reasons must be {MAX_EDIT_REASON} characters or fewer.")
    return reason or None


def _persona(row):
    return dict(row) if row else None


def list_personas(guild_id, *, database_url=None):
    with get_engine(database_url).connect() as conn:
        rows = conn.execute(select(Persona).where(Persona.guild_id == str(guild_id)).order_by(Persona.name)).mappings().all()
    return [dict(row) for row in rows]


def get_default_persona(guild_id, *, database_url=None):
    with get_engine(database_url).connect() as conn:
        row = conn.execute(select(Persona).where(Persona.guild_id == str(guild_id), Persona.is_default.is_(True))).mappings().first()
    return _persona(row)


def save_persona(guild_id, name, system_prompt, *, persona_id=None, is_default=False, database_url=None):
    name, system_prompt = " ".join(str(name or "").split()), str(system_prompt or "").strip()
    if not name or len(name) > MAX_PERSONA_NAME:
        raise AIDataError(f"Persona names must be 1 to {MAX_PERSONA_NAME} characters.")
    if not system_prompt or len(system_prompt) > MAX_PERSONA_PROMPT:
        raise AIDataError(f"Persona prompts must be 1 to {MAX_PERSONA_PROMPT} characters.")
    engine = get_engine(database_url)
    with engine.begin() as conn:
        if persona_id is None:
            result = conn.execute(insert(Persona).values(guild_id=str(guild_id), name=name, system_prompt=system_prompt, is_default=bool(is_default)))
            persona_id = result.inserted_primary_key[0]
        else:
            exists = conn.execute(select(Persona.id).where(Persona.id == int(persona_id), Persona.guild_id == str(guild_id))).scalar_one_or_none()
            if exists is None:
                return None
            conn.execute(update(Persona).where(Persona.id == int(persona_id)).values(name=name, system_prompt=system_prompt, is_default=bool(is_default)))
        if is_default:
            conn.execute(update(Persona).where(Persona.guild_id == str(guild_id), Persona.id != int(persona_id)).values(is_default=False))
        row = conn.execute(select(Persona).where(Persona.id == int(persona_id))).mappings().one()
    return dict(row)


def set_default_persona(guild_id, persona_id, *, database_url=None):
    engine = get_engine(database_url)
    with engine.begin() as conn:
        if conn.execute(select(Persona.id).where(Persona.id == int(persona_id), Persona.guild_id == str(guild_id))).scalar_one_or_none() is None:
            return None
        conn.execute(update(Persona).where(Persona.guild_id == str(guild_id)).values(is_default=False))
        conn.execute(update(Persona).where(Persona.id == int(persona_id)).values(is_default=True))
        return dict(conn.execute(select(Persona).where(Persona.id == int(persona_id))).mappings().one())


def delete_persona(guild_id, persona_id, *, database_url=None):
    with get_engine(database_url).begin() as conn:
        return conn.execute(delete(Persona).where(Persona.id == int(persona_id), Persona.guild_id == str(guild_id))).rowcount > 0


def load_conversation(channel_id, *, database_url=None):
    with get_engine(database_url).connect() as conn:
        conversation = conn.execute(select(AIConversation).where(AIConversation.channel_id == str(channel_id))).mappings().first()
        if not conversation:
            return None
        messages = conn.execute(select(AIMessage).where(AIMessage.conversation_id == conversation["id"]).order_by(AIMessage.created_at, AIMessage.id)).mappings().all()
    payload = dict(conversation)
    payload["messages"] = [dict(row) for row in messages]
    return payload


def append_exchange(channel_id, guild_id, user_text, model_text, *, token_count=0, database_url=None):
    engine = get_engine(database_url)
    with engine.begin() as conn:
        row = conn.execute(select(AIConversation).where(AIConversation.channel_id == str(channel_id))).mappings().first()
        if row is None:
            conversation_id = conn.execute(insert(AIConversation).values(channel_id=str(channel_id), guild_id=str(guild_id) if guild_id else None)).inserted_primary_key[0]
        else:
            conversation_id = row["id"]
        conn.execute(insert(AIMessage), [
            {"conversation_id": conversation_id, "role": "user", "content": user_text, "tokens": max(0, len(user_text) // 4)},
            {"conversation_id": conversation_id, "role": "model", "content": model_text, "tokens": max(0, len(model_text) // 4)},
        ])
        conn.execute(update(AIConversation).where(AIConversation.id == conversation_id).values(token_count=max(0, int(token_count))))


def compact_conversation(channel_id, summary, *, keep_messages=10, database_url=None):
    """Atomically retain the recent dialogue and replace older turns by a summary."""
    engine = get_engine(database_url)
    with engine.begin() as conn:
        conversation = conn.execute(select(AIConversation).where(AIConversation.channel_id == str(channel_id))).mappings().first()
        if not conversation:
            return False
        rows = conn.execute(select(AIMessage.id).where(AIMessage.conversation_id == conversation["id"]).order_by(AIMessage.created_at, AIMessage.id)).all()
        old_ids = [row.id for row in rows[:-keep_messages]]
        if not old_ids:
            return False
        conn.execute(delete(AIMessage).where(AIMessage.id.in_(old_ids)))
        conn.execute(update(AIConversation).where(AIConversation.id == conversation["id"]).values(rolling_summary=summary, token_count=max(0, len(summary) // 4)))
        return True


def list_conversations(guild_id, *, database_url=None):
    statement = select(AIConversation.channel_id, AIConversation.rolling_summary, AIConversation.token_count, AIConversation.updated_at, func.count(AIMessage.id).label("message_count")).join(AIMessage, AIMessage.conversation_id == AIConversation.id, isouter=True).where(AIConversation.guild_id == str(guild_id)).group_by(AIConversation.id).order_by(AIConversation.updated_at.desc())
    with get_engine(database_url).connect() as conn:
        return [dict(row) for row in conn.execute(statement).mappings().all()]


def list_history(
    guild_id,
    *,
    query=None,
    category=None,
    archived=None,
    before_id=None,
    limit=25,
    database_url=None,
):
    """List retained guild conversations for the history manager."""
    query = _validate_history_query(query)
    page = max(1, min(int(limit or 25), MAX_HISTORY_LIMIT))
    message_count = func.count(AIMessage.id).label("message_count")
    statement = (
        select(
            AIConversation.id,
            AIConversation.channel_id,
            AIConversation.category,
            AIConversation.is_archived,
            AIConversation.rolling_summary,
            AIConversation.token_count,
            AIConversation.updated_at,
            message_count,
        )
        .join(AIMessage, AIMessage.conversation_id == AIConversation.id, isouter=True)
        .where(AIConversation.guild_id == str(guild_id))
        .group_by(AIConversation.id)
        .order_by(desc(AIConversation.id))
        .limit(page + 1)
    )
    if category:
        statement = statement.where(AIConversation.category == str(category))
    if archived is not None:
        statement = statement.where(AIConversation.is_archived.is_(bool(archived)))
    if before_id is not None:
        statement = statement.where(AIConversation.id < int(before_id))
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            exists(
                select(AIMessage.id).where(
                    AIMessage.conversation_id == AIConversation.id,
                    AIMessage.content.ilike(pattern),
                ).correlate(AIConversation)
            )
        )

    with get_engine(database_url).connect() as conn:
        rows = conn.execute(statement).mappings().all()
    has_more = len(rows) > page
    entries = [
        {
            "id": row["id"],
            "channel_id": row["channel_id"],
            "category": row["category"],
            "is_archived": row["is_archived"],
            "rolling_summary": row["rolling_summary"],
            "token_count": row["token_count"],
            "message_count": row["message_count"],
            "updated_at": _iso(row["updated_at"]),
        }
        for row in rows[:page]
    ]
    return {
        "entries": entries,
        "next_cursor": entries[-1]["id"] if has_more and entries else None,
    }


def load_history(channel_id, guild_id, *, query=None, database_url=None):
    """Load one guild conversation and its retained messages."""
    query = _validate_history_query(query)
    with get_engine(database_url).connect() as conn:
        conversation = conn.execute(
            select(AIConversation)
            .where(
                AIConversation.channel_id == str(channel_id),
                AIConversation.guild_id == str(guild_id),
            )
        ).mappings().first()
        if not conversation:
            return None
        statement = select(AIMessage).where(AIMessage.conversation_id == conversation["id"])
        if query:
            statement = statement.where(AIMessage.content.ilike(f"%{query}%"))
        messages = conn.execute(
            statement.order_by(AIMessage.created_at, AIMessage.id)
        ).mappings().all()

    payload = dict(conversation)
    payload["updated_at"] = _iso(payload["updated_at"])
    payload["messages"] = [
        {
            **dict(row),
            "edited_at": _iso(row["edited_at"]),
            "redacted_at": _iso(row["redacted_at"]),
            "created_at": _iso(row["created_at"]),
        }
        for row in messages
    ]
    return payload


def edit_message(
    guild_id,
    message_id,
    content,
    *,
    editor_id,
    expected_version,
    reason=None,
    database_url=None,
):
    """Replace a retained message while preserving its previous content."""
    content = _validate_message_content(content)
    reason = _validate_reason(reason)
    engine = get_engine(database_url)
    with engine.begin() as conn:
        row = conn.execute(
            select(
                AIMessage.id,
                AIMessage.content,
                AIMessage.version,
                AIMessage.conversation_id,
            )
            .join(AIConversation, AIConversation.id == AIMessage.conversation_id)
            .where(AIMessage.id == int(message_id), AIConversation.guild_id == str(guild_id))
        ).mappings().first()
        if not row:
            return None
        if int(expected_version) != int(row["version"]):
            raise AIConflictError("This message was edited by someone else. Reload it and try again.")
        if content == row["content"]:
            return dict(row)
        conn.execute(
            insert(AIMessageRevision).values(
                message_id=row["id"],
                editor_id=str(editor_id),
                previous_content=row["content"],
                replacement_content=content,
                reason=reason,
            )
        )
        conn.execute(
            update(AIMessage)
            .where(AIMessage.id == row["id"], AIMessage.version == row["version"])
            .values(content=content, version=row["version"] + 1, edited_at=func.now())
        )
        updated = conn.execute(
            select(AIMessage).where(AIMessage.id == row["id"])
        ).mappings().one()
    return dict(updated)


def list_message_revisions(guild_id, message_id, *, database_url=None):
    statement = (
        select(AIMessageRevision)
        .join(AIMessage, AIMessage.id == AIMessageRevision.message_id)
        .join(AIConversation, AIConversation.id == AIMessage.conversation_id)
        .where(
            AIMessageRevision.message_id == int(message_id),
            AIConversation.guild_id == str(guild_id),
        )
        .order_by(desc(AIMessageRevision.id))
    )
    with get_engine(database_url).connect() as conn:
        rows = conn.execute(statement).mappings().all()
    return [
        {**dict(row), "created_at": _iso(row["created_at"])}
        for row in rows
    ]


def update_conversation_metadata(
    guild_id,
    channel_id,
    *,
    category=None,
    is_archived=None,
    database_url=None,
):
    """Update manager metadata without changing the retained transcript."""
    values = {}
    if category is not None:
        category = " ".join(str(category).split())
        if len(category) > 64:
            raise AIDataError("Categories must be 64 characters or fewer.")
        values["category"] = category or None
    if is_archived is not None:
        values["is_archived"] = bool(is_archived)
    if not values:
        raise AIDataError("At least one conversation property is required.")
    with get_engine(database_url).begin() as conn:
        conversation_id = conn.execute(
            select(AIConversation.id).where(
                AIConversation.channel_id == str(channel_id),
                AIConversation.guild_id == str(guild_id),
            )
        ).scalar_one_or_none()
        if conversation_id is None:
            return None
        conn.execute(update(AIConversation).where(AIConversation.id == conversation_id).values(**values))
        row = conn.execute(
            select(AIConversation).where(AIConversation.id == conversation_id)
        ).mappings().one()
    return {**dict(row), "updated_at": _iso(row["updated_at"])}


def purge_conversation(guild_id, channel_id, *, database_url=None):
    """Delete one channel's conversation, scoped to whoever is entitled to it.

    ``guild_id=None`` means *the DM scope*, not "no scope". ``append_exchange``
    stores DM rows with a NULL guild_id, and ``str(None)`` only ever matched the
    literal string "None", so before this a DM conversation could never be purged
    at all. Keeping it a scope rather than a bypass is what preserves the
    guarantee the web endpoint leans on: a guild caller still cannot reach another
    guild's channel or a DM, and a DM caller cannot reach a guild's channel.
    """
    scope = AIConversation.guild_id.is_(None) if guild_id is None else AIConversation.guild_id == str(guild_id)
    engine = get_engine(database_url)
    with engine.begin() as conn:
        row = conn.execute(select(AIConversation.id).where(scope, AIConversation.channel_id == str(channel_id))).scalar_one_or_none()
        if row is None:
            return False
        conn.execute(delete(AIMessage).where(AIMessage.conversation_id == row))
        return conn.execute(delete(AIConversation).where(AIConversation.id == row)).rowcount > 0
