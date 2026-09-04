"""What happens when Gemini answers 200 with no text.

The API does this: a candidate blocked by a filter, one truncated at
MAX_TOKENS, or an empty candidate list under load. It used to be reported as a
*success* carrying the literal string "I could not generate a response.", which
was then written to the channel's stored memory as the model's turn -- so the
placeholder became an example for the next reply to imitate, and a single blank
answer could repeat for the rest of the conversation.
"""

import pytest

from zephyr.services import gemini


class _Part:
    def __init__(self, text=None):
        self.text = text


class _Candidate:
    def __init__(self, finish_reason=None, parts=None):
        self.finish_reason = finish_reason
        self.content = type("Content", (), {"parts": parts})()


class _Response:
    def __init__(self, text=None, candidates=None, prompt_feedback=None):
        self.text = text
        self.candidates = candidates or []
        self.prompt_feedback = prompt_feedback
        self.usage_metadata = None


@pytest.fixture(autouse=True)
def _clean_quota():
    gemini.reset_quota_state()
    yield
    gemini.reset_quota_state()


@pytest.fixture
def answering(monkeypatch):
    """Make the model return whatever the test hands it."""

    def _install(response):
        async def fake(model_name, contents, system_personality):
            return response

        monkeypatch.setattr(gemini, "request_gemini_content", fake)

    return _install


class TestATextlessResponse:
    @pytest.mark.asyncio
    async def test_it_is_a_failed_attempt_not_a_reply(self, answering):
        answering(_Response(candidates=[_Candidate(finish_reason="SAFETY", parts=[])]))
        result = await gemini.try_generate_with_model("test-model", [], 10, "persona")
        assert result["ok"] is False
        assert result["empty"] is True
        assert "response_text" not in result

    @pytest.mark.asyncio
    async def test_a_whitespace_only_reply_counts_as_empty(self, answering):
        answering(_Response(text="", candidates=[_Candidate(parts=[_Part("")])]))
        result = await gemini.try_generate_with_model("test-model", [], 10, "persona")
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_text_still_succeeds(self, answering):
        answering(_Response(text="hello"))
        result = await gemini.try_generate_with_model("test-model", [], 10, "persona")
        assert result["ok"] is True
        assert result["response_text"] == "hello"


class TestTheDiagnosticLine:
    def test_it_names_the_finish_reason(self):
        described = gemini.describe_empty_response(
            _Response(candidates=[_Candidate(finish_reason="MAX_TOKENS", parts=[])])
        )
        assert "MAX_TOKENS" in described

    def test_it_says_so_when_there_are_no_candidates(self):
        assert "no candidates" in gemini.describe_empty_response(_Response())

    def test_it_survives_a_response_shaped_like_nothing(self):
        assert gemini.describe_empty_response(object())


class TestTheMessageThePersonSees:
    def test_it_does_not_blame_the_quota(self):
        message = gemini.build_empty_response_message(["gemini-3.1-flash-lite"])
        assert "gemini-3.1-flash-lite" in message
        assert "/token" not in message

    def test_it_names_each_model_once(self):
        message = gemini.build_empty_response_message(["a", "a", "b"])
        assert message.count("`a`") == 1
        assert "`b`" in message
