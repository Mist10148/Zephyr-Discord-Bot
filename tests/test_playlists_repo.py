"""The playlist and audit storage helpers, against a temporary SQLite file."""

import pytest
from sqlalchemy import select

from zephyr.db import audit, playlists
from zephyr.db.models import PlaylistTrack
from zephyr.db.session import get_engine


def test_save_normalises_the_name_and_keeps_track_order(db_url):
    saved = playlists.save_playlist(
        "42",
        "  Weekend   Mix  ",
        [{"title": "A", "url": "http://a", "duration_s": 10}, {"title": "B", "url": "http://b"}],
        guild_id="7",
        database_url=db_url,
    )

    stored = playlists.get_playlist(saved["id"], database_url=db_url)
    assert stored["name"] == "Weekend Mix"
    assert [track["title"] for track in stored["tracks"]] == ["A", "B"]
    assert stored["duration_s"] == 10


def test_a_track_may_have_no_url(db_url):
    """The Spotify import stores titles only; resolution happens at play time."""
    saved = playlists.save_playlist(
        "42", "Imported", [{"title": "Artist - Song", "source": "spotify"}], database_url=db_url
    )

    track = playlists.get_playlist(saved["id"], database_url=db_url)["tracks"][0]
    assert track["url"] is None
    assert track["source"] == "spotify"


def test_saving_the_same_name_twice_replaces_rather_than_duplicating(db_url):
    first = playlists.save_playlist("42", "Set", [{"title": "A"}, {"title": "B"}], database_url=db_url)
    second = playlists.save_playlist("42", "Set", [{"title": "C"}], database_url=db_url)

    assert first["id"] == second["id"]
    assert len(playlists.list_playlists("42", database_url=db_url)) == 1
    # The replaced rows are gone, not merely renumbered past.
    engine = get_engine(db_url)
    with engine.connect() as connection:
        rows = connection.execute(select(PlaylistTrack.title)).scalars().all()
    assert rows == ["C"]


def test_two_users_may_use_the_same_playlist_name(db_url):
    playlists.save_playlist("42", "Set", [{"title": "A"}], database_url=db_url)
    playlists.save_playlist("99", "Set", [{"title": "B"}], database_url=db_url)

    assert len(playlists.list_playlists("42", database_url=db_url)) == 1
    assert len(playlists.list_playlists("99", database_url=db_url)) == 1


def test_a_public_playlist_is_visible_in_its_guild_but_never_shadows_your_own(db_url):
    theirs = playlists.save_playlist(
        "99", "Set", [{"title": "Theirs"}], guild_id="7", is_public=True, database_url=db_url
    )
    playlists.update_playlist(theirs["id"], is_public=True, database_url=db_url)
    playlists.save_playlist("42", "Set", [{"title": "Mine"}], database_url=db_url)

    visible = playlists.list_playlists("42", guild_id="7", database_url=db_url)
    assert len(visible) == 2

    resolved = playlists.find_playlist("42", "set", guild_id="7", database_url=db_url)
    assert resolved["tracks"][0]["title"] == "Mine"


def test_find_falls_through_to_a_public_playlist_in_the_same_guild(db_url):
    theirs = playlists.save_playlist(
        "99", "Chill", [{"title": "Theirs"}], guild_id="7", is_public=True, database_url=db_url
    )
    playlists.update_playlist(theirs["id"], is_public=True, database_url=db_url)

    assert playlists.find_playlist("42", "chill", guild_id="7", database_url=db_url) is not None
    # A different guild cannot reach it, and neither can a caller with no guild.
    assert playlists.find_playlist("42", "chill", guild_id="8", database_url=db_url) is None
    assert playlists.find_playlist("42", "chill", database_url=db_url) is None


def test_deleting_a_playlist_takes_its_tracks_with_it(db_url):
    """SQLite ignores ON DELETE CASCADE unless PRAGMA foreign_keys is on, which is
    exactly why delete_playlist removes the children itself."""
    saved = playlists.save_playlist("42", "Set", [{"title": "A"}, {"title": "B"}], database_url=db_url)

    assert playlists.delete_playlist(saved["id"], database_url=db_url) is True
    engine = get_engine(db_url)
    with engine.connect() as connection:
        assert connection.execute(select(PlaylistTrack.title)).scalars().all() == []


def test_replace_tracks_renumbers_from_zero(db_url):
    saved = playlists.save_playlist("42", "Set", [{"title": "A"}, {"title": "B"}], database_url=db_url)
    playlists.replace_tracks(saved["id"], [{"title": "B"}, {"title": "A"}], database_url=db_url)

    stored = playlists.get_playlist(saved["id"], database_url=db_url)
    assert [track["title"] for track in stored["tracks"]] == ["B", "A"]


def test_an_empty_save_is_refused(db_url):
    with pytest.raises(playlists.PlaylistError):
        playlists.save_playlist("42", "Set", [], database_url=db_url)
    with pytest.raises(playlists.PlaylistError):
        playlists.save_playlist("42", "   ", [{"title": "A"}], database_url=db_url)


def test_tracks_with_neither_title_nor_url_are_dropped(db_url):
    saved = playlists.save_playlist(
        "42", "Set", [{"title": "A"}, {"title": "", "url": ""}], database_url=db_url
    )
    assert saved["track_count"] == 1


def test_an_audit_write_failure_never_raises(db_url, capsys):
    audit.record("settings.update", actor_id="42", guild_id="7", payload={"prefix": "!"}, database_url=db_url)
    # An unusable URL is the closest stand-in for the database being down.
    audit.record("settings.update", actor_id="42", database_url="not-a-database-url")
    assert "[Audit]" in capsys.readouterr().out


def test_an_oversized_audit_payload_is_truncated(db_url):
    from zephyr.db.models import AuditLog

    audit.record("player.play", actor_id="42", payload={"queue": ["x" * 100] * 200}, database_url=db_url)
    engine = get_engine(db_url)
    with engine.connect() as connection:
        payload = connection.execute(select(AuditLog.payload)).scalar_one()
    assert payload["truncated"] is True
