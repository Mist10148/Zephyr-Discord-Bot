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


def test_a_dm_conversation_can_be_purged(db_url):
    """A DM row stores a NULL guild_id, which ``str(None) == "None"`` never matched."""
    ai.append_exchange("20", None, "hello", "hi", database_url=db_url)

    assert ai.load_conversation("20", database_url=db_url)["guild_id"] is None
    assert ai.purge_conversation("1", "20", database_url=db_url) is False
    assert ai.purge_conversation(None, "20", database_url=db_url) is True
    assert ai.load_conversation("20", database_url=db_url) is None


def test_a_purge_cannot_cross_scopes(db_url):
    ai.append_exchange("30", "1", "question", "answer", database_url=db_url)

    assert ai.purge_conversation("2", "30", database_url=db_url) is False
    # The DM scope must not be a back door into a guild's channel either.
    assert ai.purge_conversation(None, "30", database_url=db_url) is False
    assert ai.load_conversation("30", database_url=db_url) is not None


def test_history_can_search_and_update_conversation_metadata(db_url):
    ai.append_exchange("40", "1", "find this question", "answer", database_url=db_url)
    ai.append_exchange("41", "2", "other question", "answer", database_url=db_url)

    page = ai.list_history("1", query="find", database_url=db_url)
    assert [entry["channel_id"] for entry in page["entries"]] == ["40"]

    updated = ai.update_conversation_metadata(
        "1", "40", category="support", is_archived=True, database_url=db_url
    )
    assert updated["category"] == "support"
    assert updated["is_archived"] is True
    assert ai.list_history("2", database_url=db_url)["entries"][0]["channel_id"] == "41"


def test_message_edits_keep_revisions_and_reject_stale_versions(db_url):
    ai.append_exchange("50", "1", "original", "answer", database_url=db_url)
    message = ai.load_history("50", "1", database_url=db_url)["messages"][0]

    edited = ai.edit_message(
        "1",
        "50",
        message["id"],
        "corrected",
        editor_id="900",
        expected_version=message["version"],
        reason="Fix typo",
        database_url=db_url,
    )
    assert edited["content"] == "corrected"
    assert edited["version"] == 2
    revision = ai.list_message_revisions("1", message["id"], database_url=db_url)[0]
    assert revision["previous_content"] == "original"
    assert revision["replacement_content"] == "corrected"

    try:
        ai.edit_message(
            "1",
            "50",
            message["id"],
            "stale edit",
            editor_id="901",
            expected_version=1,
            database_url=db_url,
        )
    except ai.AIConflictError:
        pass
    else:
        raise AssertionError("stale edits must be rejected")

    assert ai.edit_message(
        "2",
        "50",
        message["id"],
        "cross guild",
        editor_id="902",
        expected_version=2,
        database_url=db_url,
    ) is None


def test_history_labels_and_annotations_are_scoped(db_url):
    ai.append_exchange("60", "1", "question", "answer", database_url=db_url)
    label = ai.add_label("1", "60", "Needs review", created_by="900", database_url=db_url)
    assert label["label"] == "needs review"
    assert ai.add_label("1", "60", "Needs review", created_by="901", database_url=db_url)["id"] == label["id"]
    annotation = ai.add_annotation("1", "60", "Follow up with the moderator.", author_id="900", database_url=db_url)
    detail = ai.load_history("60", "1", database_url=db_url)
    assert [row["label"] for row in detail["labels"]] == ["needs review"]
    assert detail["annotations"][0]["note"] == annotation["note"]
    assert ai.remove_label("2", "60", label["id"], database_url=db_url) is False
    assert ai.remove_annotation("2", "60", annotation["id"], database_url=db_url) is False
    assert ai.remove_label("1", "60", label["id"], database_url=db_url)
    assert ai.remove_annotation("1", "60", annotation["id"], database_url=db_url)


def test_redaction_replaces_content_and_is_recorded_as_a_revision(db_url):
    ai.append_exchange("70", "1", "sensitive text", "answer", database_url=db_url)
    message = ai.load_history("70", "1", database_url=db_url)["messages"][0]
    redacted = ai.redact_message("1", "70", message["id"], editor_id="900", database_url=db_url)
    assert redacted["content"].startswith("[Message redacted")
    assert redacted["redacted_at"] is not None
    assert ai.list_message_revisions("1", message["id"], database_url=db_url)[0]["previous_content"] == "sensitive text"
