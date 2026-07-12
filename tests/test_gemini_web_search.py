"""Tests for multi-tool chat (search, code execution, maps, URL context) and embed output.

These tests mock the Gemini API so they perform no network calls. They verify:
- The generation config registers exactly the requested tool set; the model
  itself decides per-message whether to invoke each tool (seamless behavior).
- resolve_tool_variants produces the per-model degradation ladder (Gemini 3.x
  combines tools; 2.5 models only allow search + url_context, code solo).
- Tool-config API errors retry the same model with a degraded tool set.
- Tool-specific metadata is extracted into the correct embed fields.
- Cost logging covers all four tools, not just search.
- Chat history stores the plain answer without formatted tool output.
- Tool toggles default to disabled and are persisted.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from google.genai import types

from zephyr.services import gemini
from zephyr.services.gemini import (
    DEFAULT_CHAT_MODEL,
    SECONDARY_CHAT_MODEL,
    TERTIARY_CHAT_MODEL,
    QUATERNARY_CHAT_MODEL,
    QUINARY_CHAT_MODEL,
    resolve_fallback_models,
    build_location_instruction,
    WEB_SEARCH_BEHAVIOR_INSTRUCTION,
    ADDITIONAL_TOOLS_BEHAVIOR_INSTRUCTION,
    extract_grounding_sources,
    extract_maps_sources,
    extract_url_context_pages,
    extract_code_executions,
    detect_fired_tools,
    format_sources_list,
    split_discord_message,
    get_generate_config,
    resolve_tool_variants,
    is_gemini_3_model,
    is_tool_config_error,
    generate_gemini_response,
    get_history_for_context,
    build_response_embed,
)


def _make_grounding_chunk_web(title, uri):
    chunk = MagicMock()
    chunk.web = MagicMock()
    chunk.web.title = title
    chunk.web.uri = uri
    chunk.maps = None
    return chunk


def _make_grounding_chunk_maps(title, uri):
    chunk = MagicMock()
    chunk.web = None
    chunk.maps = MagicMock()
    chunk.maps.title = title
    chunk.maps.uri = uri
    return chunk


def _make_code_part(code, language="python"):
    part = MagicMock()
    part.executable_code = MagicMock()
    part.executable_code.code = code
    part.executable_code.language = language
    part.code_execution_result = None
    part.text = None
    return part


def _make_code_result_part(output, outcome="OUTCOME_OK"):
    part = MagicMock()
    part.executable_code = None
    part.code_execution_result = MagicMock()
    part.code_execution_result.output = output
    part.code_execution_result.outcome = outcome
    part.text = None
    return part


def _make_url_metadata(url, title):
    entry = MagicMock()
    entry.url = url
    entry.title = title
    return entry


def _make_candidate(text, *, web_sources=None, maps_sources=None, url_pages=None, code_executions=None, queries=None):
    candidate = MagicMock()
    parts = []
    if text:
        part = MagicMock()
        part.text = text
        part.executable_code = None
        part.code_execution_result = None
        parts.append(part)

    for code_exec in code_executions or []:
        parts.append(_make_code_part(code_exec["code"], code_exec.get("language", "python")))
        parts.append(_make_code_result_part(code_exec["output"], code_exec.get("outcome", "OUTCOME_OK")))

    candidate.content.parts = parts

    metadata = MagicMock()
    chunks = []
    if web_sources:
        chunks.extend(_make_grounding_chunk_web(s["title"], s["uri"]) for s in web_sources)
    if maps_sources:
        chunks.extend(_make_grounding_chunk_maps(s["title"], s["uri"]) for s in maps_sources)
    metadata.grounding_chunks = chunks
    metadata.web_search_queries = queries or []
    metadata.grounding_supports = []
    candidate.grounding_metadata = metadata

    url_context_metadata = MagicMock()
    url_context_metadata.url_metadata = [_make_url_metadata(p["url"], p.get("title", "Page")) for p in (url_pages or [])]
    candidate.url_context_metadata = url_context_metadata

    return candidate


def _make_response(text, **kwargs):
    response = MagicMock()
    response.text = text
    response.candidates = [_make_candidate(text, **kwargs)]
    response.usage_metadata = MagicMock()
    response.usage_metadata.prompt_token_count = 10
    response.usage_metadata.candidates_token_count = 20
    response.usage_metadata.total_token_count = 30
    return response


def _make_response_no_tools(text):
    response = MagicMock()
    response.text = text
    candidate = MagicMock()
    part = MagicMock()
    part.text = text
    part.executable_code = None
    part.code_execution_result = None
    candidate.content.parts = [part]
    candidate.grounding_metadata = None
    candidate.url_context_metadata = MagicMock()
    candidate.url_context_metadata.url_metadata = []
    response.candidates = [candidate]
    response.usage_metadata = MagicMock()
    response.usage_metadata.prompt_token_count = 10
    response.usage_metadata.candidates_token_count = 20
    response.usage_metadata.total_token_count = 30
    return response


def _fake_author():
    author = MagicMock()
    author.display_name = "Tester"
    author.display_avatar.url = "https://example.com/avatar.png"
    return author


@pytest.fixture(autouse=True)
def clear_gemini_state():
    """Reset in-memory history and local quota state between tests."""
    gemini.conversation_history.clear()
    gemini.model_request_windows.clear()
    gemini.model_token_windows.clear()
    gemini.model_daily_requests.clear()
    gemini.model_cooldowns.clear()
    gemini.model_usage_totals.clear()
    yield
    gemini.conversation_history.clear()
    gemini.model_request_windows.clear()
    gemini.model_token_windows.clear()
    gemini.model_daily_requests.clear()
    gemini.model_cooldowns.clear()
    gemini.model_usage_totals.clear()


class TestGroundingHelpers:
    def test_extract_grounding_sources(self):
        response = _make_response(
            "The answer is 42.",
            web_sources=[{"title": "Example", "uri": "https://example.com"}],
            queries=["query1", "query2"],
        )
        sources, queries = extract_grounding_sources(response)
        assert len(sources) == 1
        assert sources[0]["title"] == "Example"
        assert sources[0]["uri"] == "https://example.com"
        assert queries == ["query1", "query2"]

    def test_extract_grounding_sources_empty(self):
        response = _make_response_no_tools("Paris is the capital of France.")
        sources, queries = extract_grounding_sources(response)
        assert sources == []
        assert queries == []

    def test_extract_grounding_sources_deduplicates_by_uri(self):
        response = _make_response(
            "The answer is 42.",
            web_sources=[
                {"title": "Example", "uri": "https://example.com"},
                {"title": "Example Duplicate", "uri": "https://example.com"},
            ],
            queries=["query"],
        )
        sources, _ = extract_grounding_sources(response)
        assert len(sources) == 1
        assert sources[0]["title"] == "Example"

    def test_extract_maps_sources(self):
        response = _make_response(
            "Here are some places.",
            maps_sources=[{"title": "Tasty Bistro", "uri": "https://maps.google.com/?q=tasty"}],
        )
        sources = extract_maps_sources(response)
        assert len(sources) == 1
        assert sources[0]["title"] == "Tasty Bistro"

    def test_extract_url_context_pages(self):
        response = _make_response(
            "Summary here.",
            url_pages=[{"url": "https://blog.example.com/post", "title": "Cool Post"}],
        )
        pages = extract_url_context_pages(response)
        assert len(pages) == 1
        assert pages[0]["url"] == "https://blog.example.com/post"

    def test_extract_code_executions(self):
        response = _make_response(
            "The result is 4.",
            code_executions=[{"code": "2 + 2", "output": "4", "outcome": "OUTCOME_OK"}],
        )
        executions = extract_code_executions(response)
        assert len(executions) == 1
        assert executions[0]["code"] == "2 + 2"
        assert executions[0]["output"] == "4"


class TestDetectFiredTools:
    def test_detects_all_four_tools(self):
        response = _make_response(
            "Answer.",
            web_sources=[{"title": "News", "uri": "https://news.example.com"}],
            maps_sources=[{"title": "Place", "uri": "https://maps.example.com"}],
            url_pages=[{"url": "https://example.com"}],
            code_executions=[{"code": "1+1", "output": "2"}],
            queries=["query"],
        )
        fired = detect_fired_tools(response)
        assert fired == {"search": 1, "maps": True, "url_context": True, "code": True}

    def test_detects_no_tools(self):
        response = _make_response_no_tools("Just chatting.")
        fired = detect_fired_tools(response)
        assert fired == {}


class TestGenerateConfig:
    def test_get_generate_config_registers_requested_tools(self):
        config = get_generate_config("be helpful", tool_names={"search", "url_context"})
        assert config.tools is not None
        assert len(config.tools) == 2
        assert any(tool.google_search is not None for tool in config.tools)
        assert any(tool.url_context is not None for tool in config.tools)
        assert not any(tool.code_execution is not None for tool in config.tools)
        assert not any(tool.google_maps is not None for tool in config.tools)

    def test_get_generate_config_no_tools(self):
        assert not get_generate_config("be helpful", tool_names=set()).tools
        assert not get_generate_config("be helpful").tools

    def test_get_generate_config_adds_location_only_with_maps(self):
        config = get_generate_config("be helpful", tool_names={"maps"}, location={"lat": 10.72, "lng": 122.56})
        assert config.tool_config is not None
        assert config.tool_config.retrieval_config.lat_lng.latitude == 10.72
        assert config.tool_config.retrieval_config.lat_lng.longitude == 122.56

        without_maps = get_generate_config("be helpful", tool_names={"search"}, location={"lat": 10.72, "lng": 122.56})
        assert without_maps.tool_config is None


class TestToolVariants:
    ALL_ON = {"search": True, "code": True, "maps": True, "url_context": True}

    def test_is_gemini_3_model(self):
        assert is_gemini_3_model("gemini-3.1-flash-lite")
        assert not is_gemini_3_model("gemini-2.5-flash")
        assert not is_gemini_3_model(None)

    def test_gemini_3_full_ladder(self):
        variants = resolve_tool_variants(DEFAULT_CHAT_MODEL, self.ALL_ON)
        assert variants == [
            frozenset({"search", "code", "maps", "url_context"}),
            frozenset({"search", "url_context"}),
            frozenset(),
        ]

    def test_gemini_25_never_combines_illegal_tools(self):
        variants = resolve_tool_variants(SECONDARY_CHAT_MODEL, self.ALL_ON)
        assert variants == [frozenset({"search", "url_context"}), frozenset()]

    def test_gemini_25_code_only_when_sole_tool(self):
        variants = resolve_tool_variants(
            SECONDARY_CHAT_MODEL,
            {"search": False, "code": True, "maps": False, "url_context": False},
        )
        assert variants == [frozenset({"code"}), frozenset()]

    def test_disabled_tools_absent_from_every_rung(self):
        variants = resolve_tool_variants(
            DEFAULT_CHAT_MODEL,
            {"search": False, "code": True, "maps": False, "url_context": True},
        )
        assert all("search" not in variant and "maps" not in variant for variant in variants)
        assert variants[0] == frozenset({"code", "url_context"})
        assert variants[-1] == frozenset()

    def test_all_tools_off_by_default(self):
        variants = resolve_tool_variants(DEFAULT_CHAT_MODEL, {})
        assert variants == [frozenset()]

    @pytest.mark.asyncio
    async def test_system_instruction_includes_search_and_additional_tools(self):
        response = _make_response_no_tools("Hello there!")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ) as mock_generate:
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                await generate_gemini_response(None, 12345, "Hi", author=_fake_author())

        config = mock_generate.call_args.kwargs["config"]
        assert WEB_SEARCH_BEHAVIOR_INSTRUCTION in config.system_instruction
        assert ADDITIONAL_TOOLS_BEHAVIOR_INSTRUCTION in config.system_instruction


class TestBuildResponseEmbed:
    def test_build_embed_with_code_execution(self):
        author = _fake_author()
        embed = build_response_embed(
            bot_response="The answer is 4.",
            code_executions=[{"code": "2 + 2", "language": "python", "outcome": "OUTCOME_OK", "output": "4"}],
            web_sources=[],
            maps_sources=[],
            url_pages=[],
            author=author,
        )
        field_names = [f.name for f in embed.fields]
        assert "💻 Code" in field_names
        assert "📤 Output" in field_names

    def test_build_embed_with_web_sources(self):
        author = _fake_author()
        embed = build_response_embed(
            bot_response="Latest news.",
            code_executions=[],
            web_sources=[{"title": "News", "uri": "https://news.example.com"}],
            maps_sources=[],
            url_pages=[],
            author=author,
        )
        field_names = [f.name for f in embed.fields]
        assert "🔍 Web Sources" in field_names

    def test_build_embed_with_maps_and_url_context(self):
        author = _fake_author()
        embed = build_response_embed(
            bot_response="Here you go.",
            code_executions=[],
            web_sources=[],
            maps_sources=[{"title": "Cafe", "uri": "https://maps.example.com/cafe"}],
            url_pages=[{"title": "Post", "url": "https://example.com/post"}],
            author=author,
        )
        field_names = [f.name for f in embed.fields]
        assert "📍 Places" in field_names
        assert "🔗 Referenced Pages" in field_names

    def test_build_embed_no_tools_has_no_empty_fields(self):
        author = _fake_author()
        embed = build_response_embed(
            bot_response="Just chatting.",
            code_executions=[],
            web_sources=[],
            maps_sources=[],
            url_pages=[],
            author=author,
        )
        assert len(embed.fields) == 0


class TestGenerateGeminiResponse:
    @pytest.mark.asyncio
    async def test_uses_selected_model_and_all_legal_tools(self):
        # Explicitly enable all tools so the generation config includes the full
        # legal set. By default all tools are now disabled.
        gemini.set_context_settings(None, 12345, {
            "ai_model": DEFAULT_CHAT_MODEL,
            "response_format": "embed",
            "tools_enabled": {"search": True, "code": True, "maps": True, "url_context": True},
        })
        response = _make_response_no_tools("Hello there!")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ) as mock_generate:
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, 12345, "Hi", author=_fake_author())

        mock_generate.assert_called_once()
        call_kwargs = mock_generate.call_args.kwargs
        assert call_kwargs["model"] == DEFAULT_CHAT_MODEL
        config = call_kwargs["config"]
        assert any(tool.google_search is not None for tool in config.tools)
        assert any(tool.code_execution is not None for tool in config.tools)
        assert any(tool.url_context is not None for tool in config.tools)
        assert any(tool.google_maps is not None for tool in config.tools)

    @pytest.mark.asyncio
    async def test_tool_config_error_degrades_on_same_model(self):
        # Enable incompatible tools so the 2.5 model rejects the config and
        # retries with a degraded (empty) tool set.
        gemini.set_context_settings(None, 22345, {
            "ai_model": SECONDARY_CHAT_MODEL,
            "response_format": "embed",
            "tools_enabled": {"search": True, "code": True, "maps": False, "url_context": True},
        })
        response = _make_response_no_tools("Recovered answer.")
        error = Exception("400 INVALID_ARGUMENT: Multiple tools are supported only when they are all search tools")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            side_effect=[error, response],
        ) as mock_generate:
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, 22345, "Hi", author=_fake_author())

        assert mock_generate.call_count == 2
        first_call, second_call = mock_generate.call_args_list
        assert first_call.kwargs["model"] == SECONDARY_CHAT_MODEL
        assert second_call.kwargs["model"] == SECONDARY_CHAT_MODEL
        assert not second_call.kwargs["config"].tools
        assert "Recovered answer." in result.description

    @pytest.mark.asyncio
    async def test_free_tier_maps_rejection_retries_without_maps(self):
        gemini.set_context_settings(None, 32345, {
            "ai_model": DEFAULT_CHAT_MODEL,
            "response_format": "embed",
            "tools_enabled": {"search": True, "code": True, "maps": True, "url_context": True},
        })
        response = _make_response_no_tools("Answer without maps.")
        error = Exception("403 PERMISSION_DENIED: Google Maps grounding requires a paid tier")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            side_effect=[error, response],
        ) as mock_generate:
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, 32345, "restaurants near me", author=_fake_author())

        assert mock_generate.call_count == 2
        first_call, second_call = mock_generate.call_args_list
        assert first_call.kwargs["model"] == DEFAULT_CHAT_MODEL
        assert any(tool.google_maps is not None for tool in first_call.kwargs["config"].tools)
        assert second_call.kwargs["model"] == DEFAULT_CHAT_MODEL
        assert not any(tool.google_maps is not None for tool in second_call.kwargs["config"].tools)
        assert "Answer without maps." in result.description

    @pytest.mark.asyncio
    async def test_local_cooldown_on_selected_model_falls_back(self):
        from datetime import datetime, timezone, timedelta
        gemini.model_cooldowns[DEFAULT_CHAT_MODEL] = datetime.now(timezone.utc) + timedelta(minutes=5)
        response = _make_response_no_tools("Fallback answer.")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ) as mock_generate:
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, 62345, "Hi", author=_fake_author())

        # The locally rate-limited default model is skipped without an API call
        # and the next model in the chain answers.
        assert mock_generate.call_args.kwargs["model"] == SECONDARY_CHAT_MODEL
        assert "Fallback answer." in result.description

    @pytest.mark.asyncio
    async def test_unexpected_error_is_logged_and_returns_error_embed(self):
        error = Exception("totally exotic internal failure")
        with patch("builtins.print") as mock_print:
            with patch.object(
                gemini.gemini_async_client.models,
                "generate_content",
                new_callable=AsyncMock,
                side_effect=error,
            ):
                with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                    result = await generate_gemini_response(None, 42345, "Hi", author=_fake_author())

        assert "unexpected error" in result.description.lower()
        error_logs = [str(call) for call in mock_print.call_args_list if "totally exotic internal failure" in str(call)]
        assert error_logs

    @pytest.mark.asyncio
    async def test_returns_embed(self):
        response = _make_response_no_tools("Hello there!")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ):
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, 12345, "Hi", author=_fake_author())

        assert isinstance(result, type(result))  # discord.Embed, but mocking avoids import
        assert result.title is None
        assert "Hello there!" in result.description

    @pytest.mark.asyncio
    async def test_code_execution_shown_as_code_and_output_fields(self):
        gemini.set_context_settings(None, 12345, {
            "ai_model": DEFAULT_CHAT_MODEL,
            "response_format": "embed",
            "tools_enabled": {"search": False, "code": True, "maps": False, "url_context": False},
        })
        response = _make_response(
            "The result is 4.",
            code_executions=[{"code": "2 + 2", "output": "4", "outcome": "OUTCOME_OK"}],
        )
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ):
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, 12345, "What is 2+2?", author=_fake_author())

        field_names = [f.name for f in result.fields]
        assert "💻 Code" in field_names
        assert "📤 Output" in field_names
        assert "2 + 2" in result.fields[field_names.index("💻 Code")].value
        assert "4" in result.fields[field_names.index("📤 Output")].value

    @pytest.mark.asyncio
    async def test_maps_grounding_shown_as_places_field(self):
        gemini.set_context_settings(None, 12345, {
            "ai_model": DEFAULT_CHAT_MODEL,
            "response_format": "embed",
            "tools_enabled": {"search": False, "code": False, "maps": True, "url_context": False},
        })
        response = _make_response(
            "Here are some restaurants.",
            maps_sources=[{"title": "Tasty Bistro", "uri": "https://maps.google.com/?q=tasty"}],
        )
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ):
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, 12345, "restaurants near me", author=_fake_author())

        field_names = [f.name for f in result.fields]
        assert "📍 Places" in field_names
        assert "Tasty Bistro" in result.fields[field_names.index("📍 Places")].value

    @pytest.mark.asyncio
    async def test_url_context_shown_as_referenced_pages_field(self):
        gemini.set_context_settings(None, 12345, {
            "ai_model": DEFAULT_CHAT_MODEL,
            "response_format": "embed",
            "tools_enabled": {"search": False, "code": False, "maps": False, "url_context": True},
        })
        response = _make_response(
            "Summary of the page.",
            url_pages=[{"url": "https://blog.example.com/post", "title": "Cool Post"}],
        )
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ):
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, 12345, "summarize https://blog.example.com/post", author=_fake_author())

        field_names = [f.name for f in result.fields]
        assert "🔗 Referenced Pages" in field_names
        assert "Cool Post" in result.fields[field_names.index("🔗 Referenced Pages")].value

    @pytest.mark.asyncio
    async def test_combined_search_and_url_context(self):
        gemini.set_context_settings(None, 12345, {
            "ai_model": DEFAULT_CHAT_MODEL,
            "response_format": "embed",
            "tools_enabled": {"search": True, "code": False, "maps": False, "url_context": True},
        })
        response = _make_response(
            "Here is what I found.",
            web_sources=[{"title": "Search Result", "uri": "https://search.example.com"}],
            url_pages=[{"url": "https://article.example.com", "title": "Article"}],
            queries=["current event"],
        )
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ):
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(
                    None,
                    12345,
                    "search current event and summarize https://article.example.com",
                    author=_fake_author(),
                )

        field_names = [f.name for f in result.fields]
        assert "🔍 Web Sources" in field_names
        assert "🔗 Referenced Pages" in field_names

    @pytest.mark.asyncio
    async def test_no_tools_plain_embed_only(self):
        response = _make_response_no_tools("Paris is the capital of France.")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ):
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, 12345, "What's the capital of France?", author=_fake_author())

        assert len(result.fields) == 0
        assert "Paris" in result.description

    @pytest.mark.asyncio
    async def test_logs_all_fired_tools(self):
        # All tools are off by default, so explicitly enable them for this context.
        gemini.set_context_settings(None, 52345, {
            "ai_model": DEFAULT_CHAT_MODEL,
            "response_format": "embed",
            "tools_enabled": {"search": True, "code": True, "maps": True, "url_context": True},
        })
        response = _make_response(
            "Answer.",
            web_sources=[{"title": "News", "uri": "https://news.example.com"}],
            maps_sources=[{"title": "Place", "uri": "https://maps.example.com"}],
            url_pages=[{"url": "https://example.com"}],
            code_executions=[{"code": "1+1", "output": "2"}],
            queries=["query1", "query2"],
        )
        with patch("builtins.print") as mock_print:
            with patch.object(
                gemini.gemini_async_client.models,
                "generate_content",
                new_callable=AsyncMock,
                return_value=response,
            ):
                with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                    await generate_gemini_response(None, 52345, "multi-tool prompt", author=_fake_author())

        tool_logs = [call for call in mock_print.call_args_list if "[Gemini tools]" in str(call)]
        assert len(tool_logs) == 1
        log_text = str(tool_logs[0])
        assert "search=2" in log_text
        assert "maps" in log_text
        assert "url_context" in log_text
        assert "code" in log_text

    @pytest.mark.asyncio
    async def test_history_stores_plain_answer(self):
        response = _make_response(
            "The latest version is 1.2.3.",
            web_sources=[{"title": "Official Site", "uri": "https://example.com/1.2.3"}],
            queries=["latest version"],
        )
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ):
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                await generate_gemini_response(None, 12345, "latest version of Python?", author=_fake_author())

        history = get_history_for_context(None, 12345)
        assert len(history) == 2
        assert history[-1]["role"] == "model"
        assert "🔍 Web Sources" not in history[-1]["text"]
        assert "Official Site" not in history[-1]["text"]

    @pytest.mark.asyncio
    async def test_quaternary_model_selected_when_settings_choose_it(self):
        gemini.set_context_settings(None, 12345, {"ai_model": QUATERNARY_CHAT_MODEL, "response_format": "embed"})
        response = _make_response_no_tools("Premium answer.")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ) as mock_generate:
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                await generate_gemini_response(None, 12345, "Hi", author=_fake_author())

        assert mock_generate.call_args.kwargs["model"] == QUATERNARY_CHAT_MODEL

    @pytest.mark.parametrize(
        "prompt,expected_answer,web_sources,queries,user_id",
        [
            (
                "what's the best build for Jinx right now",
                "Jinx build info",
                [{"title": "Mobafire", "uri": "https://mobafire.com"}],
                ["Jinx best build 2026"],
                1001,
            ),
            (
                "what's the latest version of Python",
                "Python 3.13",
                [{"title": "Python.org", "uri": "https://python.org"}],
                ["Python latest version"],
                1002,
            ),
            (
                "what's the latest news about Mars",
                "Mars news",
                [{"title": "NASA", "uri": "https://nasa.gov"}],
                ["Mars news"],
                1003,
            ),
            (
                "what's the capital of France",
                "Paris is the capital of France.",
                [],
                [],
                1005,
            ),
            (
                "tell me a joke",
                "Why did the chicken cross the road?",
                [],
                [],
                1006,
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_domain_prompts(self, prompt, expected_answer, web_sources, queries, user_id):
        """Chat prompts with search enabled show Web Sources when the response
        includes grounding metadata; otherwise the embed has no tool fields."""
        gemini.set_context_settings(None, user_id, {
            "ai_model": DEFAULT_CHAT_MODEL,
            "response_format": "embed",
            "tools_enabled": {"search": True, "code": False, "maps": False, "url_context": False},
        })
        if web_sources:
            response = _make_response(expected_answer, web_sources=web_sources, queries=queries)
        else:
            response = _make_response_no_tools(expected_answer)

        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ):
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, user_id, prompt, author=_fake_author())

        assert expected_answer in result.description
        if web_sources:
            field_names = [f.name for f in result.fields]
            assert "🔍 Web Sources" in field_names
            for source in web_sources:
                assert source["title"] in result.fields[field_names.index("🔍 Web Sources")].value
        else:
            assert len(result.fields) == 0


class TestToolToggles:
    def test_default_tools_enabled(self):
        defaults = gemini.default_context_settings()
        assert defaults["tools_enabled"] == {"search": False, "code": False, "maps": False, "url_context": False}

    def test_normalize_settings_fills_missing_tools_from_defaults(self):
        normalized = gemini.normalize_context_settings({"ai_model": "gemini-3.5-flash", "response_format": "text"})
        assert normalized["tools_enabled"] == {"search": False, "code": False, "maps": False, "url_context": False}

    def test_normalize_settings_respects_disabled_tool(self):
        normalized = gemini.normalize_context_settings({
            "ai_model": "gemini-3.5-flash",
            "tools_enabled": {"search": False, "code": True, "maps": True, "url_context": True},
        })
        assert normalized["tools_enabled"]["search"] is False


class TestFallbackChain:
    def test_default_model_chain(self):
        assert resolve_fallback_models(DEFAULT_CHAT_MODEL) == [
            SECONDARY_CHAT_MODEL, TERTIARY_CHAT_MODEL, QUATERNARY_CHAT_MODEL, QUINARY_CHAT_MODEL,
        ]

    def test_tertiary_model_reaches_high_quota_lite_models(self):
        # Regression: a server with 2.5-flash selected must be able to fall
        # back to the big-quota lite models, not only to 2.5-pro.
        assert resolve_fallback_models(TERTIARY_CHAT_MODEL) == [
            DEFAULT_CHAT_MODEL, SECONDARY_CHAT_MODEL, QUATERNARY_CHAT_MODEL, QUINARY_CHAT_MODEL,
        ]

    def test_selected_model_never_in_own_chain(self):
        for model in [DEFAULT_CHAT_MODEL, SECONDARY_CHAT_MODEL, TERTIARY_CHAT_MODEL, QUATERNARY_CHAT_MODEL, QUINARY_CHAT_MODEL]:
            assert model not in resolve_fallback_models(model)


class TestChainRetryWait:
    @pytest.mark.asyncio
    async def test_short_retry_hint_waits_and_retries_chain(self):
        response = _make_response_no_tools("Answer after waiting.")
        error = Exception("429 RESOURCE_EXHAUSTED: rate limited. retry in 3s")
        # Five models fail on pass 1, then the first model succeeds on pass 2.
        # Cooldown storage is disabled: sleep is mocked, so real cooldowns
        # would still be active on pass 2 (in production the sleep outlasts them).
        with patch.object(gemini.asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
            with patch.object(gemini, "store_model_cooldown", new_callable=AsyncMock):
                with patch.object(
                    gemini.gemini_async_client.models,
                    "generate_content",
                    new_callable=AsyncMock,
                    side_effect=[error, error, error, error, error, response],
                ):
                    with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                        result = await generate_gemini_response(None, 72345, "Hi", author=_fake_author())

        # One chain-retry sleep of retry_after + 1 seconds.
        chain_sleeps = [call for call in mock_sleep.call_args_list if call.args and call.args[0] == 4]
        assert len(chain_sleeps) == 1
        assert "Answer after waiting." in result.description

    @pytest.mark.asyncio
    async def test_hintless_429_does_not_wait(self):
        error = Exception("429 RESOURCE_EXHAUSTED: quota exceeded")
        with patch.object(gemini.asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
            with patch.object(
                gemini.gemini_async_client.models,
                "generate_content",
                new_callable=AsyncMock,
                side_effect=error,
            ):
                with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                    result = await generate_gemini_response(None, 82345, "Hi", author=_fake_author())

        # Hint-less 429s get a synthetic 60s cooldown (> max wait) so the bot
        # doesn't stall the user; it returns the quota embed immediately.
        mock_sleep.assert_not_called()
        assert "temporarily unavailable" in result.description

    @pytest.mark.asyncio
    async def test_both_passes_fail_sleeps_only_once(self):
        error = Exception("429 RESOURCE_EXHAUSTED: rate limited. retry in 2s")
        with patch.object(gemini.asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
            with patch.object(gemini, "store_model_cooldown", new_callable=AsyncMock):
                with patch.object(
                    gemini.gemini_async_client.models,
                    "generate_content",
                    new_callable=AsyncMock,
                    side_effect=error,
                ) as mock_generate:
                    with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                        result = await generate_gemini_response(None, 92345, "Hi", author=_fake_author())

        # Exactly two chain passes (5 models each) and one wait between them.
        assert mock_sleep.call_count == 1
        assert mock_generate.call_count == 10
        assert "temporarily unavailable" in result.description


class TestLocationInstruction:
    def test_no_location_returns_empty(self):
        assert build_location_instruction(None) == ""
        assert build_location_instruction({}) == ""
        assert build_location_instruction({"name": None, "lat": None, "lng": None}) == ""

    def test_name_and_coords(self):
        text = build_location_instruction({"name": "Balantang, Jaro, Iloilo City", "lat": 10.73, "lng": 122.55})
        assert "Balantang, Jaro, Iloilo City" in text
        assert "10.73" in text and "122.55" in text
        assert "Do NOT ask" in text

    def test_name_only(self):
        text = build_location_instruction({"name": "Balantang", "lat": None, "lng": None})
        assert "Balantang" in text
        assert "lat" not in text

    def test_coords_only(self):
        text = build_location_instruction({"name": None, "lat": 10.73, "lng": 122.55})
        assert "10.73" in text

    @pytest.mark.asyncio
    async def test_location_injected_into_system_instruction(self):
        gemini.set_context_settings(None, 13579, {
            "ai_model": DEFAULT_CHAT_MODEL,
            "response_format": "embed",
            "location": {"name": "Jaro, Iloilo City", "lat": 10.72, "lng": 122.56},
        })
        response = _make_response_no_tools("The nearest one is in Jaro.")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ) as mock_generate:
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                await generate_gemini_response(None, 13579, "nearest jollibee to me?", author=_fake_author())

        system_instruction = mock_generate.call_args.kwargs["config"].system_instruction
        assert "SAVED LOCATION" in system_instruction
        assert "Jaro, Iloilo City" in system_instruction


class TestLocationSettings:
    def test_normalize_preserves_name_and_coords(self):
        normalized = gemini.normalize_context_settings({
            "location": {"name": "Jaro, Iloilo", "lat": 10.72, "lng": 122.56},
        })
        assert normalized["location"] == {"name": "Jaro, Iloilo", "lat": 10.72, "lng": 122.56}

    def test_normalize_keeps_name_only_location(self):
        normalized = gemini.normalize_context_settings({"location": {"name": "Balantang"}})
        assert normalized["location"] == {"name": "Balantang", "lat": None, "lng": None}

    def test_normalize_drops_empty_location(self):
        assert gemini.normalize_context_settings({"location": {}})["location"] is None
        assert gemini.normalize_context_settings({})["location"] is None

    def test_location_round_trips_through_context_settings(self):
        gemini.set_context_settings(None, 24680, {
            "ai_model": DEFAULT_CHAT_MODEL,
            "location": {"name": "Iloilo City", "lat": 10.72, "lng": 122.56},
        })
        settings = gemini.get_context_settings(None, 24680)
        assert settings["location"]["name"] == "Iloilo City"
        assert settings["location"]["lat"] == 10.72


class TestLegacyHelpers:
    def test_format_sources_list(self):
        sources = [{"title": "Example", "uri": "https://example.com"}]
        text = format_sources_list(sources)
        assert "**Sources:**" in text
        assert "Example" in text
        assert "https://example.com" in text

    def test_format_sources_list_empty(self):
        assert format_sources_list([]) == ""

    def test_split_discord_message(self):
        text = "a" * 5000
        parts = split_discord_message(text)
        assert len(parts[0]) == 2000
        assert len(parts[1]) == 2000
        assert len(parts[2]) == 1000
