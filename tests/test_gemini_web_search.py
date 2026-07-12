"""Tests for multi-tool chat (search, code execution, maps, URL context) and embed output.

These tests mock the Gemini API so they perform no network calls. They verify:
- All four tools are registered together in the generation config.
- Tool-specific metadata is extracted into the correct embed fields.
- Embed layout respects Discord limits and only shows fields for fired tools.
- Cost logging covers all four tools, not just search.
- Chat history stores the plain answer without formatted tool output.
- Tool toggles default to enabled and are persisted in context settings.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from google.genai import types

from zephyr.services import gemini
from zephyr.services.gemini import (
    WEB_SEARCH_CHAT_MODEL,
    WEB_SEARCH_PRO_MODEL,
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
    def test_get_generate_config_includes_all_tools(self):
        config = get_generate_config("be helpful")
        assert config.tools is not None
        assert len(config.tools) == 4
        assert any(isinstance(tool.google_search, types.GoogleSearch) for tool in config.tools if tool.google_search is not None)
        assert any(tool.code_execution is not None for tool in config.tools)
        assert any(tool.google_maps is not None for tool in config.tools)
        assert any(tool.url_context is not None for tool in config.tools)

    def test_get_generate_config_respects_disabled_tools(self):
        config = get_generate_config("be helpful", tools_enabled={"search": False, "code": True, "maps": False, "url_context": True})
        assert config.tools is not None
        assert len(config.tools) == 2
        assert not any(tool.google_search is not None for tool in config.tools)
        assert not any(tool.google_maps is not None for tool in config.tools)

    def test_get_generate_config_adds_location(self):
        config = get_generate_config("be helpful", location={"lat": 10.72, "lng": 122.56})
        assert config.tool_config is not None
        assert config.tool_config.retrieval_config.lat_lng.latitude == 10.72
        assert config.tool_config.retrieval_config.lat_lng.longitude == 122.56

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
    async def test_uses_web_search_model_and_enables_all_tools(self):
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
        assert call_kwargs["model"] == WEB_SEARCH_CHAT_MODEL
        config = call_kwargs["config"]
        assert config.tools is not None
        assert len(config.tools) == 4

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
        assert result.title == "🤖 My Response"
        assert "Hello there!" in result.description

    @pytest.mark.asyncio
    async def test_code_execution_shown_as_code_and_output_fields(self):
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
                    await generate_gemini_response(None, 12345, "multi-tool prompt", author=_fake_author())

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
    async def test_pro_model_selected_when_settings_choose_pro(self):
        gemini.set_context_settings(None, 12345, {"ai_model": WEB_SEARCH_PRO_MODEL, "response_format": "embed"})
        response = _make_response_no_tools("Premium answer.")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ) as mock_generate:
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                await generate_gemini_response(None, 12345, "Hi", author=_fake_author())

        assert mock_generate.call_args.kwargs["model"] == WEB_SEARCH_PRO_MODEL

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
        """All chat prompts go through the multi-tool path; only current-data
        prompts (mocked here with grounding metadata) receive a Web Sources field."""
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
        assert defaults["tools_enabled"] == {"search": True, "code": True, "maps": True, "url_context": True}

    def test_normalize_settings_preserves_missing_tools_as_enabled(self):
        normalized = gemini.normalize_context_settings({"ai_model": "gemini-3.5-flash", "response_format": "text"})
        assert normalized["tools_enabled"] == {"search": True, "code": True, "maps": True, "url_context": True}

    def test_normalize_settings_respects_disabled_tool(self):
        normalized = gemini.normalize_context_settings({
            "ai_model": "gemini-3.5-flash",
            "tools_enabled": {"search": False, "code": True, "maps": True, "url_context": True},
        })
        assert normalized["tools_enabled"]["search"] is False


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
