from zephyr.db import ai


def test_personas_have_one_default(db_url):
    first = ai.save_persona("1", "Helpful", "Be helpful.", is_default=True, database_url=db_url)
    second = ai.save_persona("1", "Brief", "Be brief.", is_default=True, database_url=db_url)

    assert ai.get_default_persona("1", database_url=db_url)["id"] == second["id"]
    assert {row["id"]: row["is_default"] for row in ai.list_personas("1", database_url=db_url)} == {first["id"]: False, second["id"]: True}


def test_conversation_is_guild_scoped_and_compactable(db_url):
    for index in range(6):
        ai.append_exchange("10", "1", f"question {index}", f"answer {index}", database_url=db_url)

    assert ai.list_conversations("1", database_url=db_url)[0]["message_count"] == 12
    assert ai.compact_conversation("10", "Earlier context", keep_messages=4, database_url=db_url)
    saved = ai.load_conversation("10", database_url=db_url)
    assert saved["rolling_summary"] == "Earlier context"
    assert len(saved["messages"]) == 4
    assert ai.purge_conversation("1", "10", database_url=db_url)
    assert ai.load_conversation("10", database_url=db_url) is None
