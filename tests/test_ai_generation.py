import pytest

from zephyr.services import gemini


class Response:
    def __init__(self, text):
        self.text = text
        self.usage_metadata = None


def test_extract_response_text_rejects_empty_output():
    assert gemini.extract_response_text(Response("   \n")) is None
    assert gemini.extract_response_text(Response(" useful answer ")) == "useful answer"


@pytest.fixture(autouse=True)
def clear_history():
    gemini.conversation_history.clear()
    yield
    gemini.conversation_history.clear()


@pytest.fixture
def isolated_generation(monkeypatch):
    monkeypatch.setattr(gemini.ai_db, "get_default_persona", lambda server_id: None)
    monkeypatch.setattr(gemini.ai_db, "load_conversation", lambda channel_id: None)
    monkeypatch.setattr(
        gemini,
        "trim_history_for_token_budget",
        lambda model_name, history, pending_content: _trim_history(history, pending_content),
    )


async def _trim_history(history, pending_content):
    return history, [pending_content], 1


@pytest.mark.asyncio
async def test_empty_responses_are_not_saved_and_fallback_is_attempted(monkeypatch, isolated_generation):
    attempted_models = []
    persisted = []

    monkeypatch.setattr(gemini, "resolve_fallback_models", lambda selected: ["fallback-model"])
    monkeypatch.setattr(gemini.ai_db, "append_exchange", lambda *args, **kwargs: persisted.append(args))

    async def empty_response(model_name, contents, input_tokens, system_personality):
        attempted_models.append(model_name)
        return {"ok": False, "empty_response": True}

    monkeypatch.setattr(gemini, "try_generate_with_model", empty_response)

    result = await gemini.generate_gemini_response(None, 5, "remember this", channel_id="dm-1")

    assert result == "I could not generate a response. Please try again in a moment."
    assert attempted_models == [gemini.DEFAULT_CHAT_MODEL, "fallback-model"]
    assert persisted == []
    assert gemini.get_history_for_context(None, 5) == []


@pytest.mark.asyncio
async def test_success_is_saved_once(monkeypatch, isolated_generation):
    persisted = []

    monkeypatch.setattr(gemini.ai_db, "append_exchange", lambda *args, **kwargs: persisted.append((args, kwargs)))

    async def successful_response(model_name, contents, input_tokens, system_personality):
        return {"ok": True, "response_text": "answer"}

    monkeypatch.setattr(gemini, "try_generate_with_model", successful_response)

    result = await gemini.generate_gemini_response(None, 5, "question", channel_id="dm-2")

    assert result == "answer"
    assert len(persisted) == 1
    assert persisted[0][0][0:4] == ("dm-2", None, "question", "answer")
    assert gemini.get_history_for_context(None, 5) == [
        {"role": "user", "text": "question"},
        {"role": "model", "text": "answer"},
    ]


@pytest.mark.asyncio
async def test_unexpected_generation_error_is_not_saved(monkeypatch, isolated_generation):
    persisted = []
    monkeypatch.setattr(gemini.ai_db, "append_exchange", lambda *args, **kwargs: persisted.append(args))

    async def failed_generation(model_name, contents, input_tokens, system_personality):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(gemini, "try_generate_with_model", failed_generation)

    result = await gemini.generate_gemini_response(None, 5, "do not save", channel_id="dm-3")

    assert result == "An unexpected error occurred while generating a response. Please try again in a moment."
    assert persisted == []
    assert gemini.get_history_for_context(None, 5) == []


@pytest.mark.asyncio
async def test_durable_write_error_does_not_publish_in_memory_turn(monkeypatch, isolated_generation):
    def fail_to_persist(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(gemini.ai_db, "append_exchange", fail_to_persist)

    async def successful_response(model_name, contents, input_tokens, system_personality):
        return {"ok": True, "response_text": "answer"}

    monkeypatch.setattr(gemini, "try_generate_with_model", successful_response)

    result = await gemini.generate_gemini_response(None, 5, "do not publish", channel_id="dm-4")

    assert result == "An unexpected error occurred while generating a response. Please try again in a moment."
    assert gemini.get_history_for_context(None, 5) == []


def _conversation(message_count, summary):
    messages = [
        {"role": "user" if index % 2 == 0 else "model", "content": f"turn {index}"}
        for index in range(message_count)
    ]
    return {"messages": messages, "rolling_summary": summary}


async def _one_token(model_name, contents):
    return 1


@pytest.mark.asyncio
async def test_compaction_replaces_summary_instead_of_appending(monkeypatch, isolated_generation):
    compacted = []
    summarizer_inputs = []
    monkeypatch.setattr(gemini.ai_db, "load_conversation", lambda channel_id: _conversation(22, "old summary"))
    monkeypatch.setattr(gemini.ai_db, "append_exchange", lambda *args, **kwargs: None)
    monkeypatch.setattr(gemini.ai_db, "compact_conversation", lambda channel_id, summary: compacted.append(summary))
    monkeypatch.setattr(gemini, "count_input_tokens", _one_token)

    async def respond(model_name, contents, input_tokens, system_personality):
        if system_personality.startswith("Merge"):
            summarizer_inputs.append(contents[0].parts[0].text)
            return {"ok": True, "response_text": "merged summary"}
        return {"ok": True, "response_text": "answer"}

    monkeypatch.setattr(gemini, "try_generate_with_model", respond)

    assert await gemini.generate_gemini_response(None, 5, "hi", channel_id="dm-5") == "answer"
    assert compacted == ["merged summary"]
    assert "Existing memory summary:\nold summary" in summarizer_inputs[0]
    assert "turn 0" in summarizer_inputs[0]


def test_summary_caps_keep_bounded_length():
    long_text = " ".join(f"word{index}" for index in range(2000))
    head = gemini.cap_summary_head(long_text)
    tail = gemini.cap_summary_tail(long_text)
    assert len(head) <= gemini.MAX_SUMMARY_CHARS and head.startswith("word0 ")
    assert len(tail) <= gemini.MAX_SUMMARY_CHARS and tail.endswith("word1999")
    assert gemini.cap_summary_head("short") == "short"


@pytest.mark.asyncio
async def test_oversized_stored_summary_is_capped_in_system_prompt(monkeypatch, isolated_generation):
    prompts = []
    monkeypatch.setattr(gemini.ai_db, "load_conversation", lambda channel_id: _conversation(2, "x " * 100000 + "newest"))
    monkeypatch.setattr(gemini.ai_db, "append_exchange", lambda *args, **kwargs: None)

    async def respond(model_name, contents, input_tokens, system_personality):
        prompts.append(system_personality)
        return {"ok": True, "response_text": "answer"}

    monkeypatch.setattr(gemini, "try_generate_with_model", respond)

    await gemini.generate_gemini_response(None, 5, "hi", channel_id="dm-6")
    summary = prompts[0].split("Earlier channel context summary:\n", 1)[1]
    assert len(summary) <= gemini.MAX_SUMMARY_CHARS
    assert summary.endswith("newest")


@pytest.mark.asyncio
async def test_failed_summarization_keeps_previous_summary(monkeypatch, isolated_generation):
    compacted = []
    monkeypatch.setattr(gemini.ai_db, "load_conversation", lambda channel_id: _conversation(22, "old summary"))
    monkeypatch.setattr(gemini.ai_db, "append_exchange", lambda *args, **kwargs: None)
    monkeypatch.setattr(gemini.ai_db, "compact_conversation", lambda channel_id, summary: compacted.append(summary))
    monkeypatch.setattr(gemini, "count_input_tokens", _one_token)

    async def respond(model_name, contents, input_tokens, system_personality):
        if system_personality.startswith("Merge"):
            return {"ok": False, "message": "model is on cooldown"}
        return {"ok": True, "response_text": "answer"}

    monkeypatch.setattr(gemini, "try_generate_with_model", respond)

    assert await gemini.generate_gemini_response(None, 5, "hi", channel_id="dm-7") == "answer"
    assert compacted == []


@pytest.mark.asyncio
async def test_system_instruction_counts_toward_local_tpm(monkeypatch):
    reserved = []

    async def reserve(model_name, input_tokens):
        reserved.append(input_tokens)
        return False, "limited"

    monkeypatch.setattr(gemini, "reserve_local_quota", reserve)

    result = await gemini.try_generate_with_model(gemini.DEFAULT_CHAT_MODEL, [], 10, "x" * 4000)

    assert result == {"ok": False, "message": "limited", "quota_handled": True}
    assert reserved == [10 + 1000]


class _Reason:
    def __init__(self, name):
        self.name = name


class _Blocked:
    text = None
    usage_metadata = None

    def __init__(self):
        self.prompt_feedback = type("Feedback", (), {"block_reason": _Reason("PROHIBITED_CONTENT")})()
        self.candidates = [type("Candidate", (), {"finish_reason": _Reason("SAFETY"), "content": None})()]


def test_describe_empty_response_reports_block_and_finish_reasons():
    assert gemini.describe_empty_response(_Blocked()) == "block_reason=PROHIBITED_CONTENT finish_reason=SAFETY"
    assert gemini.describe_empty_response(Response("")) == "block_reason=none finish_reason=none"


@pytest.mark.asyncio
async def test_empty_response_logs_reason(monkeypatch, capsys):
    async def allow(model_name, input_tokens):
        return True, None

    async def blocked(model_name, contents, system_personality):
        return _Blocked()

    monkeypatch.setattr(gemini, "reserve_local_quota", allow)
    monkeypatch.setattr(gemini, "request_gemini_content", blocked)

    result = await gemini.try_generate_with_model("model-x", [], 1, "system")

    assert result["empty_response"] is True
    assert "model-x: empty response (block_reason=PROHIBITED_CONTENT finish_reason=SAFETY)" in capsys.readouterr().out
