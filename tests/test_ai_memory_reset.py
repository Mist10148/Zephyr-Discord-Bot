"""Both halves of a memory reset.

``reset_conversation`` is the only place that knows the memory has two layers --
a durable row and an in-process buffer -- and it deliberately takes no Discord
types, so it can be driven directly.  The durable half runs against a real SQLite
file; the in-process half is a module global, so each test clears it.

The buffer tests are the ones that matter: a reset that only deletes the row
looks correct here and still forgets nothing in production, because the read path
falls back to the buffer whenever the row is absent.
"""

import pytest

from zephyr.db import ai as ai_db
from zephyr.services import gemini


@pytest.fixture(autouse=True)
def clear_buffer():
    gemini.conversation_history.clear()
    yield
    gemini.conversation_history.clear()


@pytest.fixture
def bot_db(monkeypatch, db_url):
    """Point the bot-side helpers -- which pass no database_url -- at the temp file.

    ``zephyr.db.session`` bound DATABASE_URL at import and ``get_engine`` reads it
    as a module global, so that is the name that has to move.
    """
    from zephyr.db import session

    monkeypatch.setattr(session, "DATABASE_URL", db_url)
    return db_url


@pytest.mark.asyncio
async def test_a_reset_clears_both_layers(bot_db):
    ai_db.append_exchange("10", "1", "remember this", "noted", database_url=bot_db)
    gemini.save_history_for_context(1, 5, [{"role": "user", "text": "remember this"}])

    assert await gemini.reset_conversation(1, 5, 10) == {"purged": True, "cached": True, "error": None}
    assert ai_db.load_conversation("10", database_url=bot_db) is None
    assert gemini.get_history_for_context(1, 5) == []


@pytest.mark.asyncio
async def test_a_dm_reset_removes_the_null_guild_row(bot_db):
    ai_db.append_exchange("11", None, "secret", "ok", database_url=bot_db)

    assert (await gemini.reset_conversation(None, 5, 11))["purged"] is True
    assert ai_db.load_conversation("11", database_url=bot_db) is None


@pytest.mark.asyncio
async def test_an_untouched_channel_reports_nothing_to_forget(bot_db):
    assert await gemini.reset_conversation(1, 5, 99) == {"purged": False, "cached": False, "error": None}


@pytest.mark.asyncio
async def test_the_buffer_is_emptied_even_when_the_database_fails(bot_db, monkeypatch):
    """The resurrection lives in the buffer, so it is cleared regardless."""

    def explode(*args, **kwargs):
        raise RuntimeError("db gone")

    gemini.save_history_for_context(1, 5, [{"role": "user", "text": "hi"}])
    monkeypatch.setattr(gemini.ai_db, "purge_conversation", explode)

    result = await gemini.reset_conversation(1, 5, 10)

    assert result["error"] == "db gone"
    assert result["cached"] is True
    assert gemini.get_history_for_context(1, 5) == []


@pytest.mark.asyncio
async def test_a_reset_does_not_touch_the_user_settings(bot_db, monkeypatch):
    """Memory and preferences share a key prefix but not a lifetime.

    ``user_settings`` is a module global loaded once at import, so it is replaced
    rather than written to -- otherwise this test's keys outlive it.
    """
    monkeypatch.setattr(gemini, "user_settings", {})
    gemini.set_context_settings(server_id=1, user_id=5, settings={"ai_model": "gemini-2.5-flash", "response_format": "text"})

    await gemini.reset_conversation(1, 5, 10)

    assert gemini.get_context_settings(1, 5)["response_format"] == "text"
