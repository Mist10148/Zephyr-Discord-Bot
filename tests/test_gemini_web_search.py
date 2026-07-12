"""Tests for web-search-aware chat grounding and source attribution.

These tests mock the Gemini API so they perform no network calls. They verify:
- Grounding sources are extracted from response metadata.
- Sources are formatted as a plain-text Discord attribution list.
- Discord messages are split within the 2000-character limit.
- Chat always uses a Gemini 3.5 model with Google Search grounding enabled.
- Sources are appended only when grounding metadata is present.
- Search query counts are logged for billing visibility.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from google.genai import types

from zephyr.services import gemini
from zephyr.services.gemini import (
    WEB_SEARCH_CHAT_MODEL,
    WEB_SEARCH_PRO_MODEL,
    WEB_SEARCH_BEHAVIOR_INSTRUCTION,
    extract_grounding_sources,
    format_sources_list,
    split_discord_message,
    get_generate_config,
    generate_gemini_response,
    get_history_for_context,
)


def _make_grounding_chunk(title, uri):
    chunk = MagicMock()
    chunk.web.title = title
    chunk.web.uri = uri
    return chunk


def _make_candidate(text, sources=None, queries=None):
    candidate = MagicMock()
    part = MagicMock()
    part.text = text
    candidate.content.parts = [part]

    metadata = MagicMock()
    metadata.grounding_chunks = [
        _make_grounding_chunk(s["title"], s["uri"]) for s in (sources or [])
    ]
    metadata.web_search_queries = queries or []
    metadata.grounding_supports = []
    candidate.grounding_metadata = metadata
    return candidate


def _make_response(text, sources=None, queries=None):
    response = MagicMock()
    response.text = text
    response.candidates = [_make_candidate(text, sources, queries)]
    response.usage_metadata = MagicMock()
    response.usage_metadata.prompt_token_count = 10
    response.usage_metadata.candidates_token_count = 20
    response.usage_metadata.total_token_count = 30
    return response


def _make_response_no_grounding(text):
    response = MagicMock()
    response.text = text
    candidate = MagicMock()
    part = MagicMock()
    part.text = text
    candidate.content.parts = [part]
    candidate.grounding_metadata = None
    response.candidates = [candidate]
    response.usage_metadata = MagicMock()
    response.usage_metadata.prompt_token_count = 10
    response.usage_metadata.candidates_token_count = 20
    response.usage_metadata.total_token_count = 30
    return response


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
            sources=[{"title": "Example", "uri": "https://example.com"}],
            queries=["query1", "query2"],
        )
        sources, queries = extract_grounding_sources(response)
        assert len(sources) == 1
        assert sources[0]["title"] == "Example"
        assert sources[0]["uri"] == "https://example.com"
        assert queries == ["query1", "query2"]

    def test_extract_grounding_sources_empty(self):
        response = _make_response_no_grounding("Paris is the capital of France.")
        sources, queries = extract_grounding_sources(response)
        assert sources == []
        assert queries == []

    def test_extract_grounding_sources_deduplicates_by_uri(self):
        response = _make_response(
            "The answer is 42.",
            sources=[
                {"title": "Example", "uri": "https://example.com"},
                {"title": "Example Duplicate", "uri": "https://example.com"},
            ],
            queries=["query"],
        )
        sources, _ = extract_grounding_sources(response)
        assert len(sources) == 1
        assert sources[0]["title"] == "Example"

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


class TestGenerateConfig:
    def test_get_generate_config_includes_google_search_tool(self):
        config = get_generate_config("be helpful", enable_google_search=True)
        assert config.tools is not None
        assert len(config.tools) == 1
        assert isinstance(config.tools[0], types.Tool)
        assert config.tools[0].google_search is not None
        assert isinstance(config.tools[0].google_search, types.GoogleSearch)

    def test_get_generate_config_omits_tool_when_disabled(self):
        config = get_generate_config("be helpful", enable_google_search=False)
        assert not config.tools


class TestGenerateGeminiResponse:
    @pytest.mark.asyncio
    async def test_uses_web_search_model_and_enables_grounding(self):
        response = _make_response_no_grounding("Hello there!")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ) as mock_generate:
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                await generate_gemini_response(None, 12345, "Hi")

        mock_generate.assert_called_once()
        call_kwargs = mock_generate.call_args.kwargs
        assert call_kwargs["model"] == WEB_SEARCH_CHAT_MODEL
        config = call_kwargs["config"]
        assert config.tools
        assert isinstance(config.tools[0], types.Tool)
        assert config.tools[0].google_search is not None

    @pytest.mark.asyncio
    async def test_system_instruction_includes_search_behavior(self):
        response = _make_response_no_grounding("Hello there!")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ) as mock_generate:
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                await generate_gemini_response(None, 12345, "Hi")

        config = mock_generate.call_args.kwargs["config"]
        assert WEB_SEARCH_BEHAVIOR_INSTRUCTION in config.system_instruction

    @pytest.mark.asyncio
    async def test_appends_sources_when_grounded(self):
        response = _make_response(
            "The latest version is 1.2.3.",
            sources=[{"title": "Official Site", "uri": "https://example.com/1.2.3"}],
            queries=["latest version"],
        )
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ):
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, 12345, "latest version of Python?")

        assert "**Sources:**" in result
        assert "Official Site" in result
        assert "https://example.com/1.2.3" in result

        # History stores the plain answer, not the rendered source list.
        history = get_history_for_context(None, 12345)
        assert len(history) == 2
        assert history[-1]["role"] == "model"
        assert "**Sources:**" not in history[-1]["text"]

    @pytest.mark.asyncio
    async def test_no_sources_when_not_grounded(self):
        response = _make_response_no_grounding("Paris is the capital of France.")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ):
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, 12345, "What's the capital of France?")

        assert "Paris" in result
        assert "**Sources:**" not in result

    @pytest.mark.asyncio
    async def test_logs_search_query_count(self):
        response = _make_response(
            "Some current event info.",
            sources=[{"title": "News Site", "uri": "https://news.example.com"}],
            queries=["current event A", "current event B"],
        )
        with patch("builtins.print") as mock_print:
            with patch.object(
                gemini.gemini_async_client.models,
                "generate_content",
                new_callable=AsyncMock,
                return_value=response,
            ):
                with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                    await generate_gemini_response(None, 12345, "latest news?")

        search_logs = [call for call in mock_print.call_args_list if "[Gemini search]" in str(call)]
        assert len(search_logs) == 1
        assert "queries=2" in str(search_logs[0])

    @pytest.mark.asyncio
    async def test_pro_model_selected_when_settings_choose_pro(self):
        gemini.set_context_settings(None, 12345, {"ai_model": WEB_SEARCH_PRO_MODEL, "response_format": "text"})
        response = _make_response_no_grounding("Premium answer.")
        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ) as mock_generate:
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                await generate_gemini_response(None, 12345, "Hi")

        assert mock_generate.call_args.kwargs["model"] == WEB_SEARCH_PRO_MODEL

    @pytest.mark.parametrize(
        "prompt,expected_answer,sources,queries,user_id",
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
                1004,
            ),
            (
                "tell me a joke",
                "Why did the chicken cross the road?",
                [],
                [],
                1005,
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_domain_prompts(self, prompt, expected_answer, sources, queries, user_id):
        """All chat prompts go through the web-search path; only current-data
        prompts (mocked here with grounding metadata) receive a Sources list."""
        if sources:
            response = _make_response(expected_answer, sources=sources, queries=queries)
        else:
            response = _make_response_no_grounding(expected_answer)

        with patch.object(
            gemini.gemini_async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=response,
        ):
            with patch.object(gemini, "count_input_tokens", new_callable=AsyncMock, return_value=10):
                result = await generate_gemini_response(None, user_id, prompt)

        assert expected_answer in result
        if sources:
            assert "**Sources:**" in result
            for source in sources:
                assert source["title"] in result
                assert source["uri"] in result
        else:
            assert "**Sources:**" not in result
