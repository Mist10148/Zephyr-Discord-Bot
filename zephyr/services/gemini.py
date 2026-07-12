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
    QUATERNARY_CHAT_MODEL,
    QUINARY_CHAT_MODEL,
)
from zephyr.services.storage import storage

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
    "gemini-2.5-pro": QUATERNARY_CHAT_MODEL,
    "gemini-3.5-flash": QUINARY_CHAT_MODEL,
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
    QUATERNARY_CHAT_MODEL: {"rpm": 5, "tpm": 250000, "rpd": 100},
    QUINARY_CHAT_MODEL: {"rpm": 10, "tpm": 250000, "rpd": 500},
}

# ---------------------------------------------------------------------------
# Tool behavior specs
# ---------------------------------------------------------------------------
# Tool selection is seamless: every enabled tool that is legal for the target
# model is registered in the generation config, and Gemini itself decides
# per-message whether to invoke each one. These instructions just steer that
# decision (see resolve_tool_variants / get_generate_config).
WEB_SEARCH_BEHAVIOR_INSTRUCTION = (
    "You have access to Google Search grounding. Use it wisely.\n\n"
    "SEARCH when a question depends on something that changes over time or could plausibly be outdated, regardless of subject:\n"
    "- Current versions/releases of software, games, apps, hardware\n"
    "- Prices, availability, exchange/crypto rates, stock info\n"
    "- Scores, standings, schedules, results (sports, esports, tournaments)\n"
    "- News, current events, ongoing situations\n"
    "- Current holder of a role/position\n"
    "- Weather, local conditions\n"
    "- Anything using words like 'latest,' 'current,' 'now,' 'today,' 'this week/month/year,' or a specific version/patch-like string\n"
    "- When genuinely unsure whether something has changed since training\n\n"
    "DO NOT search when:\n"
    "- Stable general knowledge, definitions, historical facts, math, or conceptual 'how does X work' questions\n"
    "- Casual conversation, jokes, opinions, or creative writing that doesn't hinge on current real-world data\n"
    "- The model already has solid, unchanging info to answer confidently\n\n"
    "WHEN IT DOES SEARCH: briefly note where info came from; if sources conflict or the answer is unclear, say so instead of guessing.\n\n"
    "STYLE: Discord-native — concise, casual, no essay-length replies unless explicitly asked for depth."
)

ADDITIONAL_TOOLS_BEHAVIOR_INSTRUCTION = (
    "You also have access to Code Execution, Google Maps grounding, and URL context. Use each only when it genuinely helps answer the user.\n\n"
    "CODE EXECUTION: use for math, calculations, data processing, running small scripts, or verifying a computed result instead of guessing at one.\n\n"
    "MAPS GROUNDING: use ONLY when the message has real geographic intent — a specific place, 'near me,' directions, hours, local businesses, or location-aware recommendations. "
    "Do not fire it just because a place name is mentioned in passing. "
    "Maps grounding may be unavailable; if you cannot use it, answer from web search or general knowledge instead.\n\n"
    "URL CONTEXT: use when the user pastes or references a specific link and asks about its content (summarize, compare, verify, etc.).\n\n"
    "When a tool provides data, keep the answer Discord-native and concise."
)


def build_location_instruction(location):
    """Build the system-instruction block for a saved user location.

    The location name matters most: web-search queries are text, so telling
    the model to include the place name in its searches is what makes
    'near me' questions work on the free tier (no maps grounding needed).
    """
    if not location:
        return ""
    name = location.get("name")
    lat, lng = location.get("lat"), location.get("lng")
    if not name and lat is None:
        return ""
    described = name or f"lat {lat}, lng {lng}"
    coords = f" (lat {lat}, lng {lng})" if (name and lat is not None) else ""
    return (
        f"\n\nSAVED LOCATION: The user's saved location is {described}{coords}. "
        "For any 'near me', 'nearby', or local question (restaurants, stores, weather, events, directions), "
        "use web search and include this location's name in the search query. "
        "Do NOT ask the user where they are — assume this saved location unless the message names a different place."
    )

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
# When the whole model chain is rate-limited but the shortest server retry
# hint is at most this many seconds, wait it out once and retry the chain.
CHAIN_RETRY_MAX_WAIT_SECONDS = 10
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def default_context_settings():
    return {
        "ai_model": DEFAULT_CHAT_MODEL,
        "response_format": "embed",
        # Maps grounding is a paid-tier Gemini feature, so it is off by default.
        "tools_enabled": {"search": False, "code": False, "maps": False, "url_context": False},
    }


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
    tools = normalized.get("tools_enabled")
    if not isinstance(tools, dict):
        tools = {}
    defaults = default_context_settings()["tools_enabled"]
    normalized["tools_enabled"] = {key: bool(tools.get(key, default_value)) for key, default_value in defaults.items()}
    location = normalized.get("location")
    if isinstance(location, dict):
        name = location.get("name")
        normalized["location"] = {
            "name": str(name) if name else None,
            "lat": float(location["lat"]) if location.get("lat") is not None else None,
            "lng": float(location["lng"]) if location.get("lng") is not None else None,
        }
        if normalized["location"]["name"] is None and normalized["location"]["lat"] is None:
            normalized["location"] = None
    else:
        normalized["location"] = None
    return normalized


def load_user_settings():
    global settings_store, user_settings
    settings_store = {}
    user_settings = {}
    try:
        settings_store = storage.load()
    except Exception as exc:
        print(f"Failed to load settings: {exc}")
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
        storage.save(payload)
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


def is_gemini_3_model(model_name):
    return bool(model_name) and model_name.startswith("gemini-3")


def resolve_tool_variants(model_name, tools_enabled):
    """Return the ordered tool-set ladder to attempt for a model.

    Each entry is a frozenset of tool names ("search", "code", "maps",
    "url_context"). The first entry is the most capable legal set for the
    model; later entries are degraded fallbacks tried when the API rejects the
    tool configuration. The ladder always ends with no tools at all.

    Gemini 3.x models can combine built-in tools in one request. Gemini 2.5-era
    models reject most combos ("Multiple tools are supported only when they are
    all search tools"): only google_search + url_context may be combined, and
    code_execution must be the sole tool. Maps grounding is a paid-tier feature
    and is never sent to 2.5 models.
    """
    tools_enabled = tools_enabled or {}
    defaults = default_context_settings()["tools_enabled"]
    enabled = frozenset(
        name for name, default_value in defaults.items()
        if tools_enabled.get(name, default_value)
    )

    search_tools = enabled & {"search", "url_context"}
    if is_gemini_3_model(model_name):
        ladder = [enabled, search_tools, frozenset()]
    else:
        first = search_tools if search_tools else (frozenset({"code"}) if "code" in enabled else frozenset())
        ladder = [first, frozenset()]

    deduped = []
    for variant in ladder:
        if not deduped or deduped[-1] != variant:
            deduped.append(variant)
    return deduped


TOOL_BUILDERS = {
    "search": lambda: types.Tool(google_search=types.GoogleSearch()),
    "code": lambda: types.Tool(code_execution=types.ToolCodeExecution()),
    "maps": lambda: types.Tool(google_maps=types.GoogleMaps()),
    "url_context": lambda: types.Tool(url_context=types.UrlContext()),
}


def get_generate_config(system_personality, tool_names=None, location=None):
    """Build a Gemini generation config with the given tool set.

    Registers exactly the tools in ``tool_names``; the model decides
    per-message whether to actually invoke them. The lat/lng retrieval config
    only matters for maps grounding, so it is attached only when maps is in
    the tool set.
    """
    tool_names = tool_names or frozenset()
    config_kwargs = {
        "system_instruction": system_personality,
        "safety_settings": SAFETY_SETTINGS,
    }

    enabled_tools = [TOOL_BUILDERS[name]() for name in TOOL_BUILDERS if name in tool_names]
    if enabled_tools:
        config_kwargs["tools"] = enabled_tools
    if "maps" in tool_names and location and location.get("lat") is not None and location.get("lng") is not None:
        config_kwargs["tool_config"] = types.ToolConfig(
            retrieval_config=types.RetrievalConfig(
                lat_lng=types.LatLng(latitude=location["lat"], longitude=location["lng"])
            )
        )
    return types.GenerateContentConfig(**config_kwargs)


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
    """Extract the model's text answer from the response.

    Handles plain text responses and tool-augmented responses where text may be
    mixed with code execution or grounding metadata. Returns None only when no
    text part exists.
    """
    try:
        text = getattr(response, "text", None)
        if text is not None and str(text).strip():
            return str(text).strip()
    except Exception:
        pass
    try:
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                text = getattr(part, "text", None)
                if text and str(text).strip():
                    return str(text).strip()
    except Exception:
        pass
    return None


def _first_candidate(response):
    candidates = getattr(response, "candidates", []) or []
    return candidates[0] if candidates else None


def _candidate_metadata(response):
    candidate = _first_candidate(response)
    if not candidate:
        return None
    return getattr(candidate, "grounding_metadata", None)


def extract_grounding_sources(response):
    """Extract web source titles/URIs and search queries from grounding metadata.

    Returns (web_sources, web_search_queries). Both are empty when the model
    answered without web search. Sources are de-duplicated by URI.
    """
    sources = []
    queries = []
    metadata = _candidate_metadata(response)
    if not metadata:
        return sources, queries

    queries = list(getattr(metadata, "web_search_queries", []) or [])
    seen_uris = set()
    for chunk in getattr(metadata, "grounding_chunks", []) or []:
        web = getattr(chunk, "web", None)
        if not web:
            continue
        title = getattr(web, "title", None) or "Source"
        uri = getattr(web, "uri", None)
        if uri and uri not in seen_uris:
            seen_uris.add(uri)
            sources.append({"title": title, "uri": uri})
    return sources, queries


def extract_maps_sources(response):
    """Extract Google Maps grounding place entries from grounding metadata."""
    sources = []
    metadata = _candidate_metadata(response)
    if not metadata:
        return sources
    seen_uris = set()
    for chunk in getattr(metadata, "grounding_chunks", []) or []:
        maps = getattr(chunk, "maps", None)
        if not maps:
            continue
        title = getattr(maps, "title", None) or "Place"
        uri = getattr(maps, "uri", None)
        if uri and uri not in seen_uris:
            seen_uris.add(uri)
            sources.append({"title": title, "uri": uri})
    return sources


def extract_url_context_pages(response):
    """Extract referenced page URLs from URL context metadata."""
    pages = []
    candidate = _first_candidate(response)
    if not candidate:
        return pages
    url_context_metadata = getattr(candidate, "url_context_metadata", None)
    if not url_context_metadata:
        return pages
    seen_urls = set()
    for entry in getattr(url_context_metadata, "url_metadata", []) or []:
        url = getattr(entry, "url", None)
        title = getattr(entry, "title", None) or "Page"
        if url and url not in seen_urls:
            seen_urls.add(url)
            pages.append({"title": title, "url": url})
    return pages


def extract_code_executions(response):
    """Extract code execution code/output pairs from response content parts."""
    executions = []
    candidate = _first_candidate(response)
    if not candidate:
        return executions
    content = getattr(candidate, "content", None)
    if not content:
        return executions
    current_code = None
    current_language = None
    for part in getattr(content, "parts", []) or []:
        executable_code = getattr(part, "executable_code", None)
        if executable_code:
            current_code = getattr(executable_code, "code", None) or ""
            current_language = getattr(executable_code, "language", "python") or "python"
        code_result = getattr(part, "code_execution_result", None)
        if code_result:
            outcome = getattr(code_result, "outcome", None) or ""
            output = getattr(code_result, "output", None) or ""
            executions.append({
                "code": current_code or "",
                "language": current_language or "python",
                "outcome": outcome,
                "output": output,
            })
            current_code = None
            current_language = None
    return executions


def detect_fired_tools(response, tools_enabled=None):
    """Return a dict of which tools produced metadata in this response.

    Also returns the web search query count for cost logging. Maps is logged
    separately because it costs meaningfully more than search.
    """
    tools_enabled = tools_enabled or {}
    fired = {}
    metadata = _candidate_metadata(response)
    if metadata is not None:
        queries = list(getattr(metadata, "web_search_queries", []) or [])
        if queries and tools_enabled.get("search", True):
            fired["search"] = len(queries)
        if extract_maps_sources(response) and tools_enabled.get("maps", True):
            fired["maps"] = True
    if extract_url_context_pages(response) and tools_enabled.get("url_context", True):
        fired["url_context"] = True
    if extract_code_executions(response) and tools_enabled.get("code", True):
        fired["code"] = True
    return fired


def format_sources_list(sources):
    """Format grounding sources as a plain-text list for Discord.

    Google's Grounding with Google Search terms require attribution. Discord
    cannot render the HTML search-suggestion widget, so we fall back to a
    plain-text source list. Review the latest terms before shipping publicly.
    """
    if not sources:
        return ""
    lines = ["\n\n**Sources:**"]
    for source in sources:
        title = source.get("title", "Source")
        uri = source.get("uri", "")
        lines.append(f"• {title}: {uri}")
    return "\n".join(lines)


def split_discord_message(text, limit=2000):
    """Split text into chunks that fit Discord's message length limit."""
    return [text[i:i + limit] for i in range(0, len(text), limit)]


EMBED_DESCRIPTION_LIMIT = 4096
EMBED_FIELD_VALUE_LIMIT = 1024
EMBED_TOTAL_CHAR_LIMIT = 6000
EMBED_MAX_FIELDS = 25


def _truncate(text, limit):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _format_linked_list(items, key="title", link_key="uri", fallback_url_key=None):
    lines = []
    for item in items:
        title = item.get(key, "Source")
        url = item.get(link_key)
        if not url and fallback_url_key:
            url = item.get(fallback_url_key)
        lines.append(f"• [{title}]({url})" if url else f"• {title}")
    return "\n".join(lines)


def build_response_embed(bot_response, code_executions, web_sources, maps_sources, url_pages, author=None):
    """Build a minimal Discord embed that lays out Gemini's answer plus any tool metadata.

    No title or footer — just the answer and optional tool fields. Respects
    Discord's embed limits: 4096 description chars, 1024 chars per field value,
    25 fields max, 6000 total chars. Long code/output is truncated instead of
    raising.
    """
    embed = discord.Embed(
        description=_truncate(bot_response, EMBED_DESCRIPTION_LIMIT),
        color=discord.Color.purple(),
    )

    fields = []

    for execution in code_executions or []:
        language = execution.get("language", "python")
        code = execution.get("code", "")
        output = execution.get("output", "")
        outcome = execution.get("outcome", "")
        code_block = f"```{language}\n{_truncate(code, EMBED_FIELD_VALUE_LIMIT - len(language) - 10)}\n```"
        fields.append(("💻 Code", code_block))
        output_label = "📤 Output"
        output_text = output or outcome or "No output"
        fields.append((output_label, _truncate(output_text, EMBED_FIELD_VALUE_LIMIT)))

    if web_sources:
        fields.append(("🔍 Web Sources", _truncate(_format_linked_list(web_sources), EMBED_FIELD_VALUE_LIMIT)))

    if maps_sources:
        fields.append(("📍 Places", _truncate(_format_linked_list(maps_sources, link_key="uri"), EMBED_FIELD_VALUE_LIMIT)))

    if url_pages:
        fields.append(("🔗 Referenced Pages", _truncate(_format_linked_list(url_pages, link_key="url"), EMBED_FIELD_VALUE_LIMIT)))

    total_chars = len(embed.title or "") + len(embed.description or "") + len(embed.footer.text or "")
    for name, value in fields[:EMBED_MAX_FIELDS]:
        field_total = len(name) + len(value)
        if total_chars + field_total > EMBED_TOTAL_CHAR_LIMIT:
            remaining = EMBED_TOTAL_CHAR_LIMIT - total_chars - len("⚠️") - 20
            value = _truncate(value, max(remaining, 0))
        if value:
            embed.add_field(name=name, value=value, inline=False)
            total_chars += len(name) + len(value)

    return embed


def embed_to_text(embed):
    """Render an embed as markdown text for response_format='text' fallback."""
    lines = []
    if embed.title:
        lines.append(f"**{embed.title}**")
    if embed.description:
        lines.append(embed.description)
    for field in embed.fields:
        lines.append(f"\n**{field.name}**\n{field.value}")
    if embed.footer and embed.footer.text:
        lines.append(f"\n_{embed.footer.text}_")
    return "\n".join(lines)


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


def is_tool_config_error(exc):
    """Detect API rejections of the tool configuration itself.

    Covers the 2.5-era multi-tool rejection and paid-tier-only tools (maps
    grounding on a free-tier key). Must be checked AFTER the quota/availability/
    temporary classifiers so e.g. a 429 is never treated as a tool problem.
    """
    message = str(exc).lower()
    markers = (
        "400", "invalid_argument", "invalid argument",
        "multiple tools", "only when they are all search tools",
        "tool is not supported", "not supported for this model",
        "403", "permission_denied", "permission denied", "failed_precondition",
    )
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


# Every context should be able to reach the high-quota lite models, so the
# fallback chain is "all other known models" in fixed priority order rather
# than a downward-only ladder.
FALLBACK_PRIORITY = [DEFAULT_CHAT_MODEL, SECONDARY_CHAT_MODEL, TERTIARY_CHAT_MODEL, QUATERNARY_CHAT_MODEL, QUINARY_CHAT_MODEL]


def resolve_fallback_models(selected_model):
    return [model for model in FALLBACK_PRIORITY if model != selected_model]


async def request_gemini_content(model_name, contents, system_personality, tool_names=None, location=None):
    config = get_generate_config(system_personality, tool_names=tool_names, location=location)
    return await gemini_async_client.models.generate_content(model=model_name, contents=contents, config=config)


async def try_generate_with_model(model_name, contents, input_tokens, system_personality, tools_enabled=None, location=None):
    allowed, limit_message = await reserve_local_quota(model_name, input_tokens)
    if not allowed:
        return {"ok": False, "message": limit_message, "quota_handled": True}

    # Degradation ladder: start with the most capable legal tool set for this
    # model and retry the SAME model with fewer tools if the API rejects the
    # tool configuration (2.5 multi-tool combos, paid-only maps grounding, ...).
    variants = resolve_tool_variants(model_name, tools_enabled)
    for index, tool_names in enumerate(variants):
        is_last_variant = index == len(variants) - 1
        try:
            response = await request_gemini_content(model_name, contents, system_personality, tool_names=tool_names, location=location)
            response_text = extract_response_text(response)
            if response_text is None:
                # Log the raw response shape so we can diagnose empty-text issues.
                candidate = _first_candidate(response)
                finish_reason = getattr(getattr(candidate, "finish_reason", None), "name", None) if candidate else None
                print(f"[Gemini warning] {model_name}: no text in response. finish_reason={finish_reason}")
                response_text = "I could not generate a response."
            await record_successful_usage(model_name, getattr(response, "usage_metadata", None))
            return {"ok": True, "response_text": response_text, "response": response}
        except Exception as exc:
            retry_after_seconds = parse_retry_after_seconds(exc)
            if not retry_after_seconds and is_quota_error(exc):
                # 429 with no retry hint: back off locally for a bit so every
                # message doesn't burn a doomed request on this model.
                retry_after_seconds = 60
            if retry_after_seconds:
                await store_model_cooldown(model_name, retry_after_seconds)
            if is_quota_error(exc) or is_model_availability_error(exc) or is_temporary_model_error(exc):
                # Fewer tools won't fix a quota/availability problem; move on
                # to the fallback model instead of burning ladder rungs.
                print(f"[Gemini warning] {model_name}: {str(exc).splitlines()[0]}")
                return {"ok": False, "quota_handled": True, "retry_after_seconds": retry_after_seconds, "exception": exc}
            if is_tool_config_error(exc) and not is_last_variant:
                print(f"[Gemini tool-config error] {model_name} tools={sorted(tool_names)}: {exc!r}")
                continue
            print(f"[Gemini error] {model_name} tools={sorted(tool_names)}: {exc!r}")
            traceback.print_exc()
            raise


async def generate_gemini_response(server_id, user_id, user_input, image_url=None, author=None):
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
        "\n\n"
        + WEB_SEARCH_BEHAVIOR_INSTRUCTION
        + "\n\n"
        + ADDITIONAL_TOOLS_BEHAVIOR_INSTRUCTION
    )

    settings = get_context_settings(server_id, user_id)
    selected_model = settings["ai_model"]
    # Use the model the user selected in /settings. If it's an unknown model,
    # fall back to the default. The same fallback chain is used everywhere.
    if selected_model not in MODEL_LIMITS:
        selected_model = DEFAULT_CHAT_MODEL
    fallback_models = resolve_fallback_models(selected_model)
    tools_enabled = settings.get("tools_enabled", default_context_settings()["tools_enabled"])
    location = settings.get("location")
    system_personality += build_location_instruction(location)

    try:
        history = get_history_for_context(server_id, user_id)
        image_bytes, mime_type = (None, None)
        if image_url:
            image_bytes, mime_type = await fetch_image_data(image_url)
        pending_content = build_user_content(user_input=user_input, image_bytes=image_bytes, mime_type=mime_type)
        history, request_contents, input_tokens = await trim_history_for_token_budget(selected_model, history, pending_content)
        save_history_for_context(server_id, user_id, history)

        attempt_models = [selected_model, *fallback_models]

        for chain_pass in range(2):
            attempted_fallbacks = []
            best_retry_after = None
            shortest_retry_after = None
            first_local_limit_message = None

            for index, model_name in enumerate(attempt_models):
                model_history, model_contents, model_tokens = await trim_history_for_token_budget(model_name, history, pending_content)
                result = await try_generate_with_model(model_name, model_contents, model_tokens, system_personality, tools_enabled=tools_enabled, location=location)
                retry_after = result.get("retry_after_seconds")
                if retry_after:
                    if best_retry_after is None or retry_after > best_retry_after:
                        best_retry_after = retry_after
                    if shortest_retry_after is None or retry_after < shortest_retry_after:
                        shortest_retry_after = retry_after
                if result["ok"]:
                    bot_response = result["response_text"]
                    response = result["response"]

                    # Log which tools fired for cost visibility. Maps grounding costs
                    # meaningfully more than search, so it is logged separately.
                    fired_tools = detect_fired_tools(response, tools_enabled=tools_enabled)
                    if fired_tools:
                        tool_log = ", ".join(
                            f"{tool}={value}" if isinstance(value, int) else tool
                            for tool, value in fired_tools.items()
                        )
                        print(f"[Gemini tools] model={model_name}, fired={tool_log}")

                    web_sources, _ = extract_grounding_sources(response)
                    maps_sources = extract_maps_sources(response)
                    url_pages = extract_url_context_pages(response)
                    code_executions = extract_code_executions(response)
                    any_tool_data = code_executions or web_sources or maps_sources or url_pages

                    # If the model returned no usable text and produced no tool data,
                    # treat it as a model failure and try the next fallback.
                    if bot_response == "I could not generate a response." and not any_tool_data:
                        print(f"[Gemini warning] {model_name}: empty response with no tool data; trying fallback.")
                        if index > 0:
                            attempted_fallbacks.append(model_name)
                        continue

                    # If the model produced tool output but no text, use a placeholder
                    # description so the tool fields are still visible.
                    if bot_response == "I could not generate a response." and any_tool_data:
                        bot_response = "Here's what I found:"

                    # Build a Discord embed that lays out the answer and any tool
                    # metadata in separate, clearly labeled sections.
                    embed = build_response_embed(
                        bot_response=bot_response,
                        code_executions=code_executions,
                        web_sources=web_sources,
                        maps_sources=maps_sources,
                        url_pages=url_pages,
                        author=author,
                    )

                    # Store the plain answer (without sources) in conversation history
                    # to keep tokens clean; the user still sees structured output in Discord.
                    updated_history = model_history + [
                        {"role": "user", "text": user_input or ""},
                        {"role": "model", "text": bot_response},
                    ]
                    save_history_for_context(server_id, user_id, updated_history)
                    return embed
                if result.get("message") and first_local_limit_message is None:
                    # A local rate-limit on this model shouldn't block the fallback
                    # chain; remember the message and keep trying other models.
                    first_local_limit_message = result["message"]
                if index > 0:
                    attempted_fallbacks.append(model_name)

            # Whole chain failed. Gemini-app-like patience: if some model said it
            # will recover within a few seconds, wait that out once and retry the
            # chain (cooldowns self-expire, so the recovered model is usable again).
            if chain_pass == 0 and shortest_retry_after and shortest_retry_after <= CHAIN_RETRY_MAX_WAIT_SECONDS:
                print(f"[Gemini] whole chain limited; waiting {shortest_retry_after}s and retrying once.")
                await asyncio.sleep(shortest_retry_after + 1)
                continue
            break

        return discord.Embed(
            description=first_local_limit_message
            or build_quota_message(selected_model, retry_after_seconds=best_retry_after, attempted_fallbacks=attempted_fallbacks),
            color=discord.Color.orange(),
        )
    except Exception as exc:
        print(f"[Gemini error] {selected_model}: {exc!r}")
        traceback.print_exc()
        return discord.Embed(
            description="An unexpected error occurred while generating a response. Please try again in a moment.",
            color=discord.Color.red(),
        )


async def send_response(destination, response_embed, context_obj):
    author = context_obj.user if isinstance(context_obj, discord.Interaction) else context_obj.author
    server_id = context_obj.guild.id if context_obj.guild else None
    settings = get_context_settings(server_id, author.id)
    response_format = settings["response_format"]

    if response_format == "text":
        text = embed_to_text(response_embed)
        parts = split_discord_message(text)
        for part in parts:
            await destination.send(part)
        return

    if response_format == "txt":
        text = embed_to_text(response_embed)
        if len(text) > 1900:
            try:
                file_path = f"response_{author.id}.txt"
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(text)
                await destination.send(file=discord.File(file_path))
                os.remove(file_path)
                return
            except Exception as e:
                await destination.send(f"An error occurred while creating the response file: {e}")
                return
        await destination.send(text)
        return

    await destination.send(embed=response_embed)
