"""Deleting AI rows must take their children with them on SQLite.

SQLite ignores ON DELETE CASCADE unless ``PRAGMA foreign_keys=ON`` is set, and
nothing sets it, so these assert on the child tables directly rather than
trusting the schema.
"""

from sqlalchemy import func, select

from zephyr.db import ai
from zephyr.db.models import AIConversationAnnotation, AIConversationLabel, AIMessageRevision
from zephyr.db.session import get_engine


def _count(db_url, model):
    with get_engine(db_url).connect() as conn:
        return conn.execute(select(func.count()).select_from(model)).scalar_one()


def _edit_first_message(guild_id, channel_id, db_url):
    message = ai.load_history(channel_id, guild_id, database_url=db_url)["messages"][0]
    assert ai.edit_message(
        guild_id, channel_id, message["id"], "corrected", editor_id="900", expected_version=1, database_url=db_url
    )
    return message["id"]


def test_purging_a_conversation_removes_its_revisions_labels_and_annotations(db_url):
    ai.append_exchange("60", "1", "question", "answer", database_url=db_url)
    _edit_first_message("1", "60", db_url)
    ai.add_label("1", "60", "Needs review", created_by="900", database_url=db_url)
    ai.add_annotation("1", "60", "Follow up.", author_id="900", database_url=db_url)
    assert _count(db_url, AIMessageRevision) == 1

    assert ai.purge_conversation("1", "60", database_url=db_url)

    assert _count(db_url, AIMessageRevision) == 0
    assert _count(db_url, AIConversationLabel) == 0
    assert _count(db_url, AIConversationAnnotation) == 0


def test_a_recreated_conversation_does_not_inherit_purged_labels(db_url):
    ai.append_exchange("61", "1", "question", "answer", database_url=db_url)
    ai.add_label("1", "61", "Old label", created_by="900", database_url=db_url)
    ai.purge_conversation("1", "61", database_url=db_url)

    ai.append_exchange("61", "1", "fresh", "start", database_url=db_url)

    assert ai.list_labels("1", "61", database_url=db_url) == []


def test_compaction_removes_the_revisions_of_the_messages_it_drops(db_url):
    for index in range(4):
        ai.append_exchange("62", "1", f"question {index}", f"answer {index}", database_url=db_url)
    _edit_first_message("1", "62", db_url)
    assert _count(db_url, AIMessageRevision) == 1

    assert ai.compact_conversation("62", "Summary", keep_messages=2, database_url=db_url)

    assert _count(db_url, AIMessageRevision) == 0
