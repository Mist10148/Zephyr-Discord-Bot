"""Persistence for Phase 6 conversations and guild personas."""

from sqlalchemy import delete, func, insert, select, update

from zephyr.db.models import AIConversation, AIMessage, Persona
from zephyr.db.session import get_engine

MAX_PERSONA_NAME = 64
MAX_PERSONA_PROMPT = 4000


class AIDataError(ValueError):
    pass


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
