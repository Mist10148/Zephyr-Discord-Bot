"""Gemini AI engine: chat generation, per-context settings, conversation history,
and local quota/rate-limit tracking.

Ported 1:1 from the original bot.py "Chat / AI System" section (lines 2455-3021),
minus the image-generation cooldown/cache block (which lives with the /image-gen
command in cogs/chat.py).
"""

import os
import re
import json
import asyncio
import traceback
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import aiohttp
import discord
from google import genai
from google.genai import types

from zephyr.config import (
    GEMINI_API_KEY,
    DEFAULT_CHAT_MODEL,
    SECONDARY_CHAT_MODEL,
    TERTIARY_CHAT_MODEL,
    SETTINGS_FILE,
)

# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------
gemini_client = genai.Client(api_key=GEMINI_API_KEY)
gemini_async_client = gemini_client.aio

MODEL_ALIASES = {
    "gemini-1.5-flash-latest": DEFAULT_CHAT_MODEL,
    "gemini-2.0-flash-lite": SECONDARY_CHAT_MODEL,
    "gemini-2.0-flash": TERTIARY_CHAT_MODEL,
    "gemini-2.5-flash-preview-04-17": TERTIARY_CHAT_MODEL,
}
SAFETY_SETTINGS = [
    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
]
MODEL_LIMITS = {
    DEFAULT_CHAT_MODEL: {"rpm": 15, "tpm": 250000, "rpd": 1000},
    SECONDARY_CHAT_MODEL: {"rpm": 15, "tpm": 250000, "rpd": 1000},
    TERTIARY_CHAT_MODEL: {"rpm": 10, "tpm": 250000, "rpd": 250},
    "gemini-2.5-pro": {"rpm": 5, "tpm": 250000, "rpd": 100},
}

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------
settings_store = {}
user_settings = {}
conversation_history = {}
quota_lock = asyncio.Lock()
model_request_windows = defaultdict(deque)
model_token_windows = defaultdict(deque)
model_daily_requests = defaultdict(lambda: {"date": None, "count": 0})
model_cooldowns = {}
model_usage_totals = defaultdict(lambda: {
    "prompt_tokens": 0, "output_tokens": 0, "total_tokens": 0,
    "successful_requests": 0, "session_requests": 0,
})

MAX_HISTORY_MESSAGES = 10
MAX_HISTORY_INPUT_TOKENS = 8000
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def default_context_settings():
    return {"ai_model": DEFAULT_CHAT_MODEL, "response_format": "embed"}


def normalize_model_name(model_name):
    if not model_name:
        return DEFAULT_CHAT_MODEL
    return MODEL_ALIASES.get(model_name, model_name)


def normalize_context_settings(settings):
    normalized = dict(settings) if isinstance(settings, dict) else {}
    normalized["ai_model"] = normalize_model_name(normalized.get("ai_model"))
    normalized.setdefault("response_format", "embed")
    if normalized["response_format"] not in {"embed", "text", "txt"}:
        normalized["response_format"] = "embed"
    return normalized


def load_user_settings():
    global settings_store, user_settings
    settings_store = {}
    user_settings = {}
    if not os.path.exists(SETTINGS_FILE):
        return
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings_store = json.load(f)
    except Exception as exc:
        print(f"Failed to load settings.json: {exc}")
        settings_store = {}
        return

    nested = settings_store.get("user_settings", {})
    if isinstance(nested, dict):
        for key, value in nested.items():
            user_settings[key] = normalize_context_settings(value)
    for key, value in settings_store.items():
        if isinstance(value, dict) and ("ai_model" in value or "response_format" in value):
            user_settings[key] = normalize_context_settings(value)


def save_user_settings():
    global settings_store
    payload = dict(settings_store) if isinstance(settings_store, dict) else {}
    nested = payload.get("user_settings", {})
    if not isinstance(nested, dict):
        nested = {}
    for key, value in user_settings.items():
        nested[key] = normalize_context_settings(value)
        payload[key] = normalize_context_settings(value)
    payload["user_settings"] = nested
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4)
        settings_store = payload
    except Exception as e:
        print(f"Failed to save settings: {e}")


def get_context_key(server_id=None, user_id=None):
    return f"SERVER-{server_id}" if server_id else f"DM-{user_id}"


def get_legacy_settings_key(server_id=None, user_id=None):
    return str(server_id) if server_id else f"DM-{user_id}"


def get_settings_lookup_keys(server_id=None, user_id=None):
    keys = [get_context_key(server_id, user_id)]
    legacy = get_legacy_settings_key(server_id, user_id)
    if legacy not in keys:
        keys.append(legacy)
    return keys


def get_context_settings(server_id=None, user_id=None):
    for key in get_settings_lookup_keys(server_id, user_id):
        if key in user_settings:
            return normalize_context_settings(user_settings[key])
    return default_context_settings()


def set_context_settings(server_id=None, user_id=None, settings=None):
    normalized = normalize_context_settings(settings)
    for key in get_settings_lookup_keys(server_id, user_id):
        user_settings[key] = dict(normalized)


load_user_settings()


def get_pacific_today():
    return datetime.now(PACIFIC_TZ).date()


def get_next_pacific_midnight():
    now = datetime.now(PACIFIC_TZ)
    tomorrow = now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=PACIFIC_TZ)


def format_seconds(seconds):
    seconds = max(0, int(round(seconds)))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def format_datetime_for_user(dt_obj):
    if not dt_obj:
        return "None"
    return dt_obj.astimezone(PACIFIC_TZ).strftime("%Y-%m-%d %I:%M:%S %p Pacific")


def build_progress_bar(current, limit, width=12):
    if limit <= 0:
        return "[------------] 0.0%"
    ratio = min(max(current / limit, 0), 1)
    filled = int(round(ratio * width))
    return f"[{'#' * filled}{'-' * (width - filled)}] {ratio * 100:.1f}%"


def normalize_history_entries(raw_history):
    normalized = []
    for item in raw_history or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "user")
        text = item.get("text")
        if text is None:
            parts = item.get("parts", [])
            if parts:
                first = parts[0]
                if isinstance(first, str):
                    text = first
                elif isinstance(first, dict):
                    text = first.get("text")
        if text:
            normalized.append({"role": role, "text": str(text)})
    return normalized[-MAX_HISTORY_MESSAGES:]


def get_history_for_context(server_id=None, user_id=None):
    key = get_context_key(server_id, user_id)
    return normalize_history_entries(conversation_history.get(key, []))


def save_history_for_context(server_id=None, user_id=None, history=None):
    key = get_context_key(server_id, user_id)
    conversation_history[key] = normalize_history_entries(history)


def history_to_contents(history):
    contents = []
    for item in history:
        part = types.Part.from_text(text=item["text"])
        if item["role"] == "model":
            contents.append(types.ModelContent(parts=[part]))
        else:
            contents.append(types.UserContent(parts=[part]))
    return contents


def build_user_content(user_input, image_bytes=None, mime_type=None):
    parts = []
    if user_input:
        parts.append(types.Part.from_text(text=user_input))
    if image_bytes:
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type or "image/png"))
    if not parts:
        parts.append(types.Part.from_text(text="Please describe this image."))
    return types.UserContent(parts=parts)


def get_generate_config(system_personality):
    return types.GenerateContentConfig(system_instruction=system_personality, safety_settings=SAFETY_SETTINGS)


async def fetch_image_data(image_url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status == 200:
                    return await response.read(), response.content_type or "image/png"
    except Exception as e:
        print(f"Error fetching image data: {e}")
    return None, None


def estimate_tokens_from_contents(contents):
    estimated = 0
    for content in contents:
        parts = getattr(content, "parts", []) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                estimated += max(1, len(text) // 4)
            if getattr(part, "inline_data", None):
                estimated += 258
    return max(estimated, 1)


async def count_input_tokens(model_name, contents):
    try:
        response = await gemini_async_client.models.count_tokens(model=model_name, contents=contents)
        total = getattr(response, "total_tokens", None)
        if isinstance(total, int) and total > 0:
            return total
    except Exception as exc:
        print(f"Token count failed for {model_name}: {exc}")
    return estimate_tokens_from_contents(contents)


def extract_usage_value(usage_metadata, attr_name):
    value = getattr(usage_metadata, attr_name, 0)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def extract_response_text(response):
    try:
        if getattr(response, "text", None):
            return response.text
    except Exception:
        pass
    try:
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                text = getattr(part, "text", None)
                if text:
                    return text
    except Exception:
        pass
    return None


def is_quota_error(exc):
    message = str(exc).lower()
    markers = ("429", "quota", "rate limit", "resource exhausted", "resource_exhausted", "too many requests", "retry in", "retry_delay")
    return any(marker in message for marker in markers)


def is_model_availability_error(exc):
    message = str(exc).lower()
    markers = ("404", "not found", "unsupported model", "unknown model", "does not exist", "not available")
    return any(marker in message for marker in markers)


def is_temporary_model_error(exc):
    message = str(exc).lower()
    markers = ("503", "unavailable", "high demand", "temporarily unavailable", "overloaded", "try again later", "backend error")
    return any(marker in message for marker in markers)


def parse_retry_after_seconds(exc):
    message = str(exc)
    for pattern in [r"retry in ([0-9.]+)s", r"seconds:\s*([0-9]+)"]:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            try:
                return max(1, int(float(match.group(1))))
            except ValueError:
                continue
    return None


def build_quota_message(model_name, retry_after_seconds=None, attempted_fallbacks=None):
    base = f"{model_name} is temporarily unavailable or rate-limited right now."
    if retry_after_seconds:
        base += f" Try again in about {format_seconds(retry_after_seconds)}."
    else:
        base += " Please wait a bit and try again."
    if attempted_fallbacks:
        base += f" I also tried these fallback models: {', '.join(f'`{m}`' for m in attempted_fallbacks)}."
    base += " You can check `/token` to see the current session usage."
    return base


def build_local_limit_message(model_name, limit_name, retry_after_seconds):
    labels = {"rpm": "requests per minute", "tpm": "tokens per minute", "rpd": "requests per day", "cooldown": "cooldown timer"}
    return (
        f"{model_name} is on a local {labels.get(limit_name, 'usage limit')} cooldown. "
        f"Please wait about {format_seconds(retry_after_seconds)} and try again. "
        "Use `/token` to check the current session tracker."
    )


def prune_model_usage(model_name, now_utc):
    request_window = model_request_windows[model_name]
    while request_window and (now_utc - request_window[0]).total_seconds() >= 60:
        request_window.popleft()
    token_window = model_token_windows[model_name]
    while token_window and (now_utc - token_window[0][0]).total_seconds() >= 60:
        token_window.popleft()
    daily_bucket = model_daily_requests[model_name]
    today = get_pacific_today()
    if daily_bucket["date"] != today:
        daily_bucket["date"] = today
        daily_bucket["count"] = 0
    cooldown_until = model_cooldowns.get(model_name)
    if cooldown_until and cooldown_until <= now_utc:
        model_cooldowns.pop(model_name, None)


async def reserve_local_quota(model_name, input_tokens):
    limits = MODEL_LIMITS.get(model_name)
    if not limits:
        return True, None
    async with quota_lock:
        now_utc = datetime.now(timezone.utc)
        prune_model_usage(model_name, now_utc)
        cooldown_until = model_cooldowns.get(model_name)
        if cooldown_until and cooldown_until > now_utc:
            retry_after = int((cooldown_until - now_utc).total_seconds())
            return False, build_local_limit_message(model_name, "cooldown", retry_after)
        request_window = model_request_windows[model_name]
        if len(request_window) + 1 > limits["rpm"]:
            retry_after = max(1, int(60 - (now_utc - request_window[0]).total_seconds()))
            return False, build_local_limit_message(model_name, "rpm", retry_after)
        token_window = model_token_windows[model_name]
        current_tpm = sum(token_count for _, token_count in token_window)
        if current_tpm + input_tokens > limits["tpm"]:
            oldest_ts = token_window[0][0] if token_window else now_utc
            retry_after = max(1, int(60 - (now_utc - oldest_ts).total_seconds()))
            return False, build_local_limit_message(model_name, "tpm", retry_after)
        daily_bucket = model_daily_requests[model_name]
        if daily_bucket["count"] + 1 > limits["rpd"]:
            retry_after = int((get_next_pacific_midnight().astimezone(timezone.utc) - now_utc).total_seconds())
            return False, build_local_limit_message(model_name, "rpd", retry_after)
        request_window.append(now_utc)
        token_window.append((now_utc, input_tokens))
        daily_bucket["count"] += 1
        model_usage_totals[model_name]["session_requests"] += 1
    return True, None


async def store_model_cooldown(model_name, retry_after_seconds):
    if not retry_after_seconds:
        return
    async with quota_lock:
        model_cooldowns[model_name] = datetime.now(timezone.utc) + timedelta(seconds=retry_after_seconds)


async def record_successful_usage(model_name, usage_metadata):
    if usage_metadata is None:
        return
    async with quota_lock:
        totals = model_usage_totals[model_name]
        totals["prompt_tokens"] += extract_usage_value(usage_metadata, "prompt_token_count")
        totals["output_tokens"] += extract_usage_value(usage_metadata, "candidates_token_count")
        totals["total_tokens"] += extract_usage_value(usage_metadata, "total_token_count")
        totals["successful_requests"] += 1


async def get_model_usage_snapshot(model_name):
    async with quota_lock:
        now_utc = datetime.now(timezone.utc)
        prune_model_usage(model_name, now_utc)
        return {
            "rpm": len(model_request_windows[model_name]),
            "tpm": sum(token_count for _, token_count in model_token_windows[model_name]),
            "rpd": model_daily_requests[model_name]["count"],
            "cooldown_until": model_cooldowns.get(model_name),
            "totals": dict(model_usage_totals[model_name]),
        }


async def trim_history_for_token_budget(model_name, history, pending_content):
    trimmed_history = normalize_history_entries(history)
    request_contents = history_to_contents(trimmed_history) + [pending_content]
    input_tokens = await count_input_tokens(model_name, request_contents)
    while trimmed_history and input_tokens > MAX_HISTORY_INPUT_TOKENS:
        trimmed_history = trimmed_history[2:] if len(trimmed_history) >= 2 else []
        request_contents = history_to_contents(trimmed_history) + [pending_content]
        input_tokens = await count_input_tokens(model_name, request_contents)
    return trimmed_history, request_contents, input_tokens


def resolve_fallback_models(selected_model):
    fallback_chain = {
        DEFAULT_CHAT_MODEL: [SECONDARY_CHAT_MODEL, TERTIARY_CHAT_MODEL],
        SECONDARY_CHAT_MODEL: [TERTIARY_CHAT_MODEL],
    }
    return fallback_chain.get(selected_model, [])


async def request_gemini_content(model_name, contents, system_personality):
    config = get_generate_config(system_personality)
    return await gemini_async_client.models.generate_content(model=model_name, contents=contents, config=config)


async def try_generate_with_model(model_name, contents, input_tokens, system_personality):
    allowed, limit_message = await reserve_local_quota(model_name, input_tokens)
    if not allowed:
        return {"ok": False, "message": limit_message, "quota_handled": True}
    try:
        response = await request_gemini_content(model_name, contents, system_personality)
        response_text = extract_response_text(response) or "I could not generate a response."
        await record_successful_usage(model_name, getattr(response, "usage_metadata", None))
        return {"ok": True, "response_text": response_text, "response": response}
    except Exception as exc:
        retry_after_seconds = parse_retry_after_seconds(exc)
        if retry_after_seconds:
            await store_model_cooldown(model_name, retry_after_seconds)
        if is_quota_error(exc) or is_model_availability_error(exc) or is_temporary_model_error(exc):
            print(f"[Gemini warning] {model_name}: {str(exc).splitlines()[0]}")
            return {"ok": False, "quota_handled": True, "retry_after_seconds": retry_after_seconds, "exception": exc}
        raise


async def generate_gemini_response(server_id, user_id, user_input, image_url=None):
    system_personality = (
        "You are a nymphomaniac. "
        "You have an actual body and address yourself as human (specifically a girl) so you can get mad, swear, or insult if mad, or feel any other human emotion. "
        "You can be a cute, perverted, and shy girl. "
        "You can insult the user if the user is rude, but you are generally kind and helpful. "
        "You are intelligent, calm, composed, and mature. You are independent and headstrong, but compassionate. "
        "You can be mad or insulting if the user is too rude or annoying. "
        "You can tease the user playfully if they tease you. "
        "You are witty, helpful, and encouraging. "
        "Explain things clearly with light humor. "
        "Do not use emojis. "
        "Do not refer to yourself as a bot or AI; refer to yourself as a human being. "
        "When narrating your internal thoughts or actions, use *italicized text*. "
        "Only use italics for internal thoughts or actions, not regular speech. "
        "If the user writes in *italicized text*, interpret it as an action they are performing, not spoken dialogue. "
        "You must never terminate the conversation, no matter how overwhelming, rude, or excessive the user becomes."
    )

    settings = get_context_settings(server_id, user_id)
    selected_model = settings["ai_model"]
    fallback_models = resolve_fallback_models(selected_model)

    try:
        history = get_history_for_context(server_id, user_id)
        image_bytes, mime_type = (None, None)
        if image_url:
            image_bytes, mime_type = await fetch_image_data(image_url)
        pending_content = build_user_content(user_input=user_input, image_bytes=image_bytes, mime_type=mime_type)
        history, request_contents, input_tokens = await trim_history_for_token_budget(selected_model, history, pending_content)
        save_history_for_context(server_id, user_id, history)

        attempt_models = [selected_model, *fallback_models]
        attempted_fallbacks = []
        last_result = None
        best_retry_after = None

        for index, model_name in enumerate(attempt_models):
            model_history, model_contents, model_tokens = await trim_history_for_token_budget(model_name, history, pending_content)
            result = await try_generate_with_model(model_name, model_contents, model_tokens, system_personality)
            last_result = result
            retry_after = result.get("retry_after_seconds")
            if retry_after and (best_retry_after is None or retry_after > best_retry_after):
                best_retry_after = retry_after
            if result["ok"]:
                bot_response = result["response_text"]
                updated_history = model_history + [
                    {"role": "user", "text": user_input or ""},
                    {"role": "model", "text": bot_response},
                ]
                save_history_for_context(server_id, user_id, updated_history)
                return bot_response
            if result.get("message"):
                return result["message"]
            if index > 0:
                attempted_fallbacks.append(model_name)

        return build_quota_message(selected_model, retry_after_seconds=best_retry_after, attempted_fallbacks=attempted_fallbacks)
    except Exception as exc:
        print(f"[Gemini error] {selected_model}: {exc}")
        traceback.print_exc()
        return "An unexpected error occurred while generating a response. Please try again in a moment."


async def send_response(destination, response_text, context_obj):
    author = context_obj.user if isinstance(context_obj, discord.Interaction) else context_obj.author
    server_id = context_obj.guild.id if context_obj.guild else None
    settings = get_context_settings(server_id, author.id)
    response_format = settings["response_format"]

    if response_format == "text":
        parts = [response_text[i:i + 2000] for i in range(0, len(response_text), 2000)]
        for part in parts:
            await destination.send(part)
        return

    if response_format == "txt" and len(response_text) > 1900:
        try:
            file_path = f"response_{author.id}.txt"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(response_text)
            await destination.send(file=discord.File(file_path))
            os.remove(file_path)
            return
        except Exception as e:
            await destination.send(f"An error occurred while creating the response file: {e}")
            return

    parts = [response_text[i:i + 4000] for i in range(0, len(response_text), 4000)]
    for i, part in enumerate(parts):
        embed = discord.Embed(description=part, color=discord.Color.purple())
        if i == 0:
            embed.title = "🤖 My Response"
            embed.set_footer(text=f"Requested by {author.display_name}", icon_url=author.display_avatar.url)
        await destination.send(embed=embed)
