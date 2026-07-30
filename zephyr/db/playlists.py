"""Playlist persistence, on SQLAlchemy Core.

Core rather than the ORM for the reason ``website/repo.py`` already gives: there
is no sessionmaker anywhere in Zephyr, and these are a handful of statements.
There *is* a parent/child relationship here, but it is traversed in exactly one
direction and always eagerly, so an identity map would buy nothing.

Everything is synchronous.  The bot must therefore call these through
``asyncio.to_thread`` -- the same discipline ``client.py`` uses for the guild
snapshot, and the bug 625c4ba already fixed once for settings persistence.  The
web tier calls them directly, because a Flask worker thread is allowed to block.
"""

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from zephyr.db.models import Playlist, PlaylistTrack
from zephyr.db.session import get_engine

# A queue can legitimately hold a 200-entry playlist (yt-dlp's max_entries), and
# saving one must not be silently truncated to something smaller.
MAX_TRACKS = 500
MAX_NAME_LENGTH = 80


class PlaylistError(RuntimeError):
    """A playlist operation failed for a reason the caller should show a user."""


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise PlaylistError("A playlist needs a name.")
    if len(name) > MAX_NAME_LENGTH:
        raise PlaylistError(f"Playlist names are limited to {MAX_NAME_LENGTH} characters.")
    return name


def _clean_tracks(tracks: list[dict]) -> list[dict]:
    """Normalise incoming track dicts and drop the unusable ones.

    A row with neither a title nor a URL cannot be played *or* re-resolved, so it
    is dropped rather than stored as a permanent failure.
    """
    cleaned = []
    for track in tracks[:MAX_TRACKS]:
        title = (track.get("title") or "").strip()
        url = (track.get("url") or "").strip() or None
        if not title and not url:
            continue
        try:
            duration = int(track.get("duration_s") or 0)
        except (TypeError, ValueError):
            duration = 0
        cleaned.append(
            {
                "title": title or url,
                "url": url,
                "duration_s": max(0, duration),
                "source": (track.get("source") or "youtube").strip() or "youtube",
            }
        )
    return cleaned


def _rows_to_tracks(rows) -> list[dict]:
    return [
        {"title": row.title, "url": row.url, "duration_s": row.duration_s, "source": row.source}
        for row in rows
    ]


def save_playlist(
    owner_id: str,
    name: str,
    tracks: list[dict],
    *,
    guild_id: str | None = None,
    is_public: bool = False,
    database_url: str | None = None,
) -> dict:
    """Create the playlist, or replace the tracks of the owner's existing one.

    One transaction, so a failed save never leaves a playlist half-rewritten --
    which for a replace would mean losing the old queue without storing the new
    one.  The IntegrityError branch covers two callers racing the same new name:
    the loser re-reads and replaces instead of surfacing a constraint violation.
    """
    name = _clean_name(name)
    cleaned = _clean_tracks(tracks)
    if not cleaned:
        raise PlaylistError("There is nothing to save.")

    engine = get_engine(database_url)
    with engine.begin() as connection:
        playlist_id = connection.execute(
            select(Playlist.id).where(Playlist.owner_id == str(owner_id), Playlist.name == name)
        ).scalar_one_or_none()

        if playlist_id is None:
            try:
                playlist_id = connection.execute(
                    insert(Playlist)
                    .values(
                        owner_id=str(owner_id),
                        guild_id=str(guild_id) if guild_id else None,
                        name=name,
                        is_public=bool(is_public),
                    )
                    .returning(Playlist.id)
                ).scalar_one()
            except IntegrityError:
                raise PlaylistError(f"A playlist called **{name}** already exists.") from None
        else:
            connection.execute(
                delete(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id)
            )

        connection.execute(
            insert(PlaylistTrack),
            [{"playlist_id": playlist_id, "position": index, **track} for index, track in enumerate(cleaned)],
        )

    return {"id": playlist_id, "name": name, "track_count": len(cleaned)}


def list_playlists(
    owner_id: str,
    *,
    guild_id: str | None = None,
    database_url: str | None = None,
) -> list[dict]:
    """The caller's playlists, plus any public ones saved in ``guild_id``.

    A LEFT JOIN so an empty playlist still appears -- it is a real thing the user
    created and hiding it would look like the save failed.
    """
    engine = get_engine(database_url)
    visible = Playlist.owner_id == str(owner_id)
    if guild_id:
        visible = visible | ((Playlist.guild_id == str(guild_id)) & Playlist.is_public.is_(True))

    statement = (
        select(
            Playlist.id,
            Playlist.owner_id,
            Playlist.guild_id,
            Playlist.name,
            Playlist.is_public,
            Playlist.created_at,
            func.count(PlaylistTrack.position).label("track_count"),
            func.coalesce(func.sum(PlaylistTrack.duration_s), 0).label("duration_s"),
        )
        .join(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id, isouter=True)
        .where(visible)
        .group_by(Playlist.id)
        .order_by(Playlist.name)
    )
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [dict(row) for row in rows]


def get_playlist(playlist_id: int, *, database_url: str | None = None) -> dict | None:
    """A playlist and its tracks in order, or None."""
    engine = get_engine(database_url)
    with engine.connect() as connection:
        header = connection.execute(
            select(
                Playlist.id,
                Playlist.owner_id,
                Playlist.guild_id,
                Playlist.name,
                Playlist.is_public,
                Playlist.created_at,
            ).where(Playlist.id == playlist_id)
        ).mappings().first()
        if header is None:
            return None
        rows = connection.execute(
            select(PlaylistTrack)
            .where(PlaylistTrack.playlist_id == playlist_id)
            .order_by(PlaylistTrack.position)
        ).all()
    payload = dict(header)
    payload["tracks"] = _rows_to_tracks(rows)
    payload["track_count"] = len(payload["tracks"])
    payload["duration_s"] = sum(track["duration_s"] for track in payload["tracks"])
    return payload


def find_playlist(
    owner_id: str,
    name: str,
    *,
    guild_id: str | None = None,
    database_url: str | None = None,
) -> dict | None:
    """Resolve a playlist by name for ``/load``.

    The owner's own playlist wins over a public one with the same name, so
    somebody else sharing a name in the server can never shadow yours.
    """
    engine = get_engine(database_url)
    name = " ".join((name or "").split())
    if not name:
        return None
    with engine.connect() as connection:
        candidates = connection.execute(
            select(Playlist.id, Playlist.owner_id).where(func.lower(Playlist.name) == name.lower())
        ).all()

    mine = next((row.id for row in candidates if row.owner_id == str(owner_id)), None)
    if mine is not None:
        return get_playlist(mine, database_url=database_url)
    if not guild_id:
        return None
    for row in candidates:
        playlist = get_playlist(row.id, database_url=database_url)
        if playlist and playlist["is_public"] and str(playlist["guild_id"] or "") == str(guild_id):
            return playlist
    return None


def replace_tracks(playlist_id: int, tracks: list[dict], *, database_url: str | None = None) -> int:
    """Rewrite a playlist's tracks wholesale.

    Position is half the primary key, so a reorder is not an UPDATE of one row --
    it is a new list.  Delete-then-insert inside one transaction is both simpler
    than shuffling positions around a unique constraint and immune to the
    intermediate states that would violate it.
    """
    cleaned = _clean_tracks(tracks)
    engine = get_engine(database_url)
    with engine.begin() as connection:
        connection.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id))
        if cleaned:
            connection.execute(
                insert(PlaylistTrack),
                [
                    {"playlist_id": playlist_id, "position": index, **track}
                    for index, track in enumerate(cleaned)
                ],
            )
    return len(cleaned)


def update_playlist(
    playlist_id: int,
    *,
    name: str | None = None,
    is_public: bool | None = None,
    database_url: str | None = None,
) -> bool:
    values: dict = {}
    if name is not None:
        values["name"] = _clean_name(name)
    if is_public is not None:
        values["is_public"] = bool(is_public)
    if not values:
        return False
    engine = get_engine(database_url)
    with engine.begin() as connection:
        try:
            result = connection.execute(
                update(Playlist).where(Playlist.id == playlist_id).values(**values)
            )
        except IntegrityError:
            raise PlaylistError("You already have a playlist with that name.") from None
    return result.rowcount > 0


def delete_playlist(playlist_id: int, *, database_url: str | None = None) -> bool:
    """Delete a playlist and its tracks.

    The tracks are deleted explicitly rather than left to ON DELETE CASCADE:
    SQLite enforces foreign keys only when ``PRAGMA foreign_keys=ON`` is set per
    connection, which nothing here does, so the cascade would silently not happen
    on the default development database and orphan every row.
    """
    engine = get_engine(database_url)
    with engine.begin() as connection:
        connection.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id))
        result = connection.execute(delete(Playlist).where(Playlist.id == playlist_id))
    return result.rowcount > 0
