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


def _history_bool(name):
    raw = request.args.get(name)
    if raw is None:
        return None
    if raw not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false.")
    return raw == "true"


def _history_int(name):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    if not raw.isdigit():
        raise ValueError(f"{name} must be a positive integer.")
    return int(raw)

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


@api.get("/guilds/<guild_id>/ai/history")
@guild_scoped
def history(guild_id):
    try:
        archived = _history_bool("archived")
        before = _history_int("before")
        limit = _history_int("limit")
        page = ai_db.list_history(
            guild_id,
            query=request.args.get("q"),
            category=request.args.get("category"),
            archived=archived,
            before_id=before,
            limit=limit or 25,
            database_url=current_app.config["DATABASE_URL"],
        )
    except (ValueError, ai_db.AIDataError) as exc:
        return error("invalid_query", str(exc), 400)
    return jsonify({"id": guild_id, **page})


@api.get("/guilds/<guild_id>/ai/history/<channel_id>")
@guild_scoped
def history_detail(guild_id, channel_id):
    try:
        conversation = ai_db.load_history(
            channel_id,
            guild_id,
            query=request.args.get("q"),
            database_url=current_app.config["DATABASE_URL"],
        )
    except ai_db.AIDataError as exc:
        return error("invalid_query", str(exc), 400)
    if conversation is None:
        return error("not_found", "Conversation not found.", 404)
    return jsonify(conversation)


@api.patch("/guilds/<guild_id>/ai/history/<channel_id>")
@guild_scoped
def update_history(guild_id, channel_id):
    body = _body()
    if body is None or set(body) - {"category", "is_archived"}:
        return error("invalid_body", "Send category and/or is_archived.", 400)
    try:
        conversation = ai_db.update_conversation_metadata(
            guild_id,
            channel_id,
            category=body.get("category"),
            is_archived=body.get("is_archived"),
            database_url=current_app.config["DATABASE_URL"],
        )
    except ai_db.AIDataError as exc:
        return error("invalid_value", str(exc), 400)
    if conversation is None:
        return error("not_found", "Conversation not found.", 404)
    if "category" in body:
        _audit(guild_id, "ai.history.category", {"channel_id": str(channel_id), "category": conversation["category"]})
    if "is_archived" in body:
        _audit(guild_id, "ai.history.archive" if conversation["is_archived"] else "ai.history.unarchive", {"channel_id": str(channel_id)})
    return jsonify(conversation)


@api.patch("/guilds/<guild_id>/ai/history/<channel_id>/messages/<int:message_id>")
@guild_scoped
def edit_history_message(guild_id, channel_id, message_id):
    body = _body()
    required = {"content", "expected_version"}
    if body is None or set(body) - required - {"reason"} or not required <= set(body):
        return error("invalid_body", "Send content and expected_version, with an optional reason.", 400)
    try:
        message = ai_db.edit_message(
            guild_id,
            channel_id,
            message_id,
            body["content"],
            editor_id=g.zephyr_session.user_id,
            expected_version=body["expected_version"],
            reason=body.get("reason"),
            database_url=current_app.config["DATABASE_URL"],
        )
    except ai_db.AIConflictError as exc:
        return error("conflict", str(exc), 409)
    except (TypeError, ValueError, ai_db.AIDataError) as exc:
        return error("invalid_body", str(exc), 400)
    if message is None:
        return error("not_found", "Message not found.", 404)
    _audit(guild_id, "ai.history.message.edit", {"channel_id": str(channel_id), "message_id": message_id, "version": message["version"]})
    return jsonify(message)


@api.post("/guilds/<guild_id>/ai/history/<channel_id>/messages/<int:message_id>/redact")
@guild_scoped
def redact_history_message(guild_id, channel_id, message_id):
    body = _body()
    if body is None or set(body) - {"reason"}:
        return error("invalid_body", "Send an optional reason.", 400)
    try:
        message = ai_db.redact_message(
            guild_id,
            channel_id,
            message_id,
            editor_id=g.zephyr_session.user_id,
            reason=body.get("reason"),
            database_url=current_app.config["DATABASE_URL"],
        )
    except ai_db.AIDataError as exc:
        return error("invalid_body", str(exc), 400)
    if message is None:
        return error("not_found", "Message not found.", 404)
    _audit(guild_id, "ai.history.message.redact", {"channel_id": str(channel_id), "message_id": message_id, "version": message["version"]})
    return jsonify(message)


@api.get("/guilds/<guild_id>/ai/history/<channel_id>/messages/<int:message_id>/revisions")
@guild_scoped
def message_revisions(guild_id, channel_id, message_id):
    conversation = ai_db.load_history(channel_id, guild_id, database_url=current_app.config["DATABASE_URL"])
    if conversation is None or not any(message["id"] == message_id for message in conversation["messages"]):
        return error("not_found", "Message not found.", 404)
    return jsonify({"revisions": ai_db.list_message_revisions(guild_id, message_id, database_url=current_app.config["DATABASE_URL"])})


@api.get("/guilds/<guild_id>/ai/history/<channel_id>/labels")
@guild_scoped
def history_labels(guild_id, channel_id):
    if ai_db.load_history(channel_id, guild_id, database_url=current_app.config["DATABASE_URL"]) is None:
        return error("not_found", "Conversation not found.", 404)
    return jsonify({"labels": ai_db.list_labels(guild_id, channel_id, database_url=current_app.config["DATABASE_URL"])})


@api.post("/guilds/<guild_id>/ai/history/<channel_id>/labels")
@guild_scoped
def add_history_label(guild_id, channel_id):
    body = _body()
    if body is None or set(body) != {"label"}:
        return error("invalid_body", "Send a label.", 400)
    try:
        label = ai_db.add_label(guild_id, channel_id, body["label"], created_by=g.zephyr_session.user_id, database_url=current_app.config["DATABASE_URL"])
    except ai_db.AIDataError as exc:
        return error("invalid_value", str(exc), 400)
    if label is None:
        return error("not_found", "Conversation not found.", 404)
    _audit(guild_id, "ai.history.label.add", {"channel_id": str(channel_id), "label_id": label["id"]})
    return jsonify(label), 201


@api.delete("/guilds/<guild_id>/ai/history/<channel_id>/labels/<int:label_id>")
@guild_scoped
def remove_history_label(guild_id, channel_id, label_id):
    if not ai_db.remove_label(guild_id, channel_id, label_id, database_url=current_app.config["DATABASE_URL"]):
        return error("not_found", "Label not found.", 404)
    _audit(guild_id, "ai.history.label.remove", {"channel_id": str(channel_id), "label_id": label_id})
    return "", 204


@api.get("/guilds/<guild_id>/ai/history/<channel_id>/annotations")
@guild_scoped
def history_annotations(guild_id, channel_id):
    if ai_db.load_history(channel_id, guild_id, database_url=current_app.config["DATABASE_URL"]) is None:
        return error("not_found", "Conversation not found.", 404)
    return jsonify({"annotations": ai_db.list_annotations(guild_id, channel_id, database_url=current_app.config["DATABASE_URL"])})


@api.post("/guilds/<guild_id>/ai/history/<channel_id>/annotations")
@guild_scoped
def add_history_annotation(guild_id, channel_id):
    body = _body()
    if body is None or set(body) != {"note"}:
        return error("invalid_body", "Send a note.", 400)
    try:
        annotation = ai_db.add_annotation(guild_id, channel_id, body["note"], author_id=g.zephyr_session.user_id, database_url=current_app.config["DATABASE_URL"])
    except ai_db.AIDataError as exc:
        return error("invalid_value", str(exc), 400)
    if annotation is None:
        return error("not_found", "Conversation not found.", 404)
    _audit(guild_id, "ai.history.annotation.add", {"channel_id": str(channel_id), "annotation_id": annotation["id"]})
    return jsonify(annotation), 201


@api.delete("/guilds/<guild_id>/ai/history/<channel_id>/annotations/<int:annotation_id>")
@guild_scoped
def remove_history_annotation(guild_id, channel_id, annotation_id):
    if not ai_db.remove_annotation(guild_id, channel_id, annotation_id, database_url=current_app.config["DATABASE_URL"]):
        return error("not_found", "Annotation not found.", 404)
    _audit(guild_id, "ai.history.annotation.remove", {"channel_id": str(channel_id), "annotation_id": annotation_id})
    return "", 204

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
