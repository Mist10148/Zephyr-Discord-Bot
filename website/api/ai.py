"""Manager-only AI dashboard resources."""
from flask import current_app, g, jsonify, request

from website.api import api, error
from website.api.guard import guild_scoped
from website.api.player import bridge_call
from zephyr.db import ai as ai_db
from zephyr.db import audit
from zephyr.services import bridge

# The purge has already committed by the time this is sent, so it waits only
# briefly: the bot's answer is needed for promptness, never for correctness.
MEMORY_CACHE_TIMEOUT = 2.0

def _body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None

def _audit(guild_id, action, payload=None):
    audit.record(action, actor_id=g.zephyr_session.user_id, guild_id=guild_id, payload=payload, source="web", database_url=current_app.config["DATABASE_URL"])

@api.get("/guilds/<guild_id>/ai/personas")
@guild_scoped
def personas(guild_id):
    return jsonify({"personas": ai_db.list_personas(guild_id, database_url=current_app.config["DATABASE_URL"])})

@api.post("/guilds/<guild_id>/ai/personas")
@guild_scoped
def create_persona(guild_id):
    body = _body()
    if body is None or set(body) - {"name", "system_prompt", "is_default"}:
        return error("invalid_body", "Send name, system_prompt, and optional is_default.", 400)
    try:
        persona = ai_db.save_persona(guild_id, body.get("name"), body.get("system_prompt"), is_default=bool(body.get("is_default")), database_url=current_app.config["DATABASE_URL"])
    except ai_db.AIDataError as exc:
        return error("invalid_value", str(exc), 400)
    _audit(guild_id, "ai.persona.create", {"persona_id": persona["id"]})
    return jsonify(persona), 201

@api.patch("/guilds/<guild_id>/ai/personas/<int:persona_id>")
@guild_scoped
def patch_persona(guild_id, persona_id):
    body = _body()
    if body is None or set(body) - {"name", "system_prompt", "is_default"} or "name" not in body or "system_prompt" not in body:
        return error("invalid_body", "Send name and system_prompt, with optional is_default.", 400)
    try:
        persona = ai_db.save_persona(guild_id, body["name"], body["system_prompt"], persona_id=persona_id, is_default=bool(body.get("is_default")), database_url=current_app.config["DATABASE_URL"])
    except ai_db.AIDataError as exc:
        return error("invalid_value", str(exc), 400)
    if persona is None: return error("not_found", "Persona not found.", 404)
    _audit(guild_id, "ai.persona.update", {"persona_id": persona_id})
    return jsonify(persona)

@api.delete("/guilds/<guild_id>/ai/personas/<int:persona_id>")
@guild_scoped
def remove_persona(guild_id, persona_id):
    if not ai_db.delete_persona(guild_id, persona_id, database_url=current_app.config["DATABASE_URL"]): return error("not_found", "Persona not found.", 404)
    _audit(guild_id, "ai.persona.delete", {"persona_id": persona_id})
    return "", 204

@api.post("/guilds/<guild_id>/ai/personas/<int:persona_id>/default")
@guild_scoped
def default_persona(guild_id, persona_id):
    persona = ai_db.set_default_persona(guild_id, persona_id, database_url=current_app.config["DATABASE_URL"])
    if persona is None: return error("not_found", "Persona not found.", 404)
    _audit(guild_id, "ai.persona.default", {"persona_id": persona_id})
    return jsonify(persona)

@api.get("/guilds/<guild_id>/ai/memory")
@guild_scoped
def memories(guild_id):
    return jsonify({"conversations": ai_db.list_conversations(guild_id, database_url=current_app.config["DATABASE_URL"])})

@api.get("/guilds/<guild_id>/ai/memory/<channel_id>")
@guild_scoped
def memory_detail(guild_id, channel_id):
    conversation = ai_db.load_conversation(channel_id, database_url=current_app.config["DATABASE_URL"])
    if not conversation or str(conversation.get("guild_id")) != str(guild_id): return error("not_found", "Memory not found.", 404)
    return jsonify(conversation)

def _clear_bot_memory_cache(guild_id, channel_id):
    """Best-effort: ask the running bot to drop its in-process buffer too.

    The stored row is only half the memory. The bot keeps a rolling buffer per
    guild and falls back to it whenever a channel has no row, so a purge that
    stops at the database is undone by the next message. This cannot be a hard
    dependency, unlike bridge_call: the delete is already durable, and reporting
    a completed purge as a failure would invite a retry that then 404s.
    """
    redis_url = current_app.config["REDIS_URL"]
    if not redis_url:
        return
    try:
        bridge.send_command("ai.memory.purge", guild_id=guild_id, actor_id=g.zephyr_session.user_id, args={"channel_id": str(channel_id)}, timeout=MEMORY_CACHE_TIMEOUT, url=redis_url)
    except Exception as exc:
        print(f"[AI] Could not clear the bot's memory buffer for channel {channel_id}: {exc}")

@api.delete("/guilds/<guild_id>/ai/memory/<channel_id>")
@guild_scoped
def purge_memory(guild_id, channel_id):
    if not ai_db.purge_conversation(guild_id, channel_id, database_url=current_app.config["DATABASE_URL"]): return error("not_found", "Memory not found.", 404)
    _clear_bot_memory_cache(guild_id, channel_id)
    _audit(guild_id, "ai.memory.purge", {"channel_id": channel_id})
    return "", 204

@api.get("/guilds/<guild_id>/ai/usage")
@guild_scoped
def usage(guild_id):
    return bridge_call("ai.usage", guild_id=guild_id, actor_id=g.zephyr_session.user_id)
