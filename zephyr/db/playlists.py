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


class PlaylistExists(PlaylistError):
    """``save_playlist(replace=False)`` found the name already taken."""


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise PlaylistError("A playlist needs a name.")
    if len(name) > MAX_NAME_LENGTH:
        raise PlaylistError(f"Playlist names are limited to {MAX_NAME_LENGTH} characters.")
    return name


def _name_key(name: str) -> str:
    """How playlist names are compared: case-insensitively, in any language.

    Done in Python, not with SQL ``lower()``: SQLite's lower() folds ASCII
    only, so "Été" never matched "été" -- a second /save hit the unique
    constraint, and /load and /playlist-delete could not find the playlist.
    """
    return " ".join((name or "").split()).casefold()


def _owner_rows(connection, owner_id: str, name: str):
    """The owner's playlists whose name matches ``name``, oldest first."""
    rows = connection.execute(
        select(Playlist.id, Playlist.name)
        .where(Playlist.owner_id == str(owner_id))
        .order_by(Playlist.id)
    ).all()
    key = _name_key(name)
    return [row for row in rows if _name_key(row.name) == key]


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
    replace: bool = True,
    database_url: str | None = None,
) -> dict:
    """Create the playlist, or replace the tracks of the owner's existing one.

    ``replace=False`` raises ``PlaylistExists`` instead of replacing: /save
    means "overwrite my playlist of that name", but an import does not, and
    silently swapping out a hand-built playlist's tracks lost them.

    One transaction, so a failed save never leaves a playlist half-rewritten --
    which for a replace would mean losing the old queue without storing the new
    one.  Two callers racing the same new name hit the unique constraint; the
    loser gets ``PlaylistExists`` rather than a raw constraint violation.

    Names match case-insensitively, the same way ``find_playlist`` resolves
    them: saving "chill" next to "Chill" created two rows, and /load or
    /playlist-delete then acted on whichever the database returned first.
    """
    name = _clean_name(name)
    cleaned = _clean_tracks(tracks)
    if not cleaned:
        raise PlaylistError("There is nothing to save.")

    engine = get_engine(database_url)
    with engine.begin() as connection:
        existing = _owner_rows(connection, owner_id, name)
        # An exact match first; otherwise the owner's playlist differing only in case.
        exact = next((row.id for row in existing if row.name == name), None)
        playlist_id = exact if exact is not None else (existing[0].id if existing else None)

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
                raise PlaylistExists(f"A playlist called **{name}** already exists.") from None
        elif not replace:
            raise PlaylistExists(f"A playlist called **{name}** already exists.")
        else:
            connection.execute(
                delete(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id)
            )
            # /save weekend public:True on an existing private playlist replaced
            # the tracks but silently left it private.
            values = {"is_public": bool(is_public), "name": name}
            if guild_id:
                values["guild_id"] = str(guild_id)
            connection.execute(update(Playlist).where(Playlist.id == playlist_id).values(**values))

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
    key = _name_key(name)
    with engine.connect() as connection:
        own = _owner_rows(connection, owner_id, name)
        shared = []
        if not own and guild_id:
            shared = [
                row for row in connection.execute(
                    select(Playlist.id, Playlist.name)
                    .where(Playlist.guild_id == str(guild_id), Playlist.is_public.is_(True))
                    .order_by(Playlist.id)
                ).all()
                if _name_key(row.name) == key
            ]

    if own:
        # Prefer the exact spelling if older data holds names differing only in case.
        exact = [row for row in own if row.name == name]
        return get_playlist((exact or own)[0].id, database_url=database_url)
    if shared:
        return get_playlist(shared[0].id, database_url=database_url)
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
        if "name" in values:
            # Case-insensitive like every other lookup: a rename to "chill" next
            # to "Chill" passed the exact-match unique constraint and brought
            # back the ambiguity /save and /load were fixed to avoid.
            owner = connection.execute(
                select(Playlist.owner_id).where(Playlist.id == playlist_id)
            ).scalar()
            if owner is not None and any(
                row.id != playlist_id for row in _owner_rows(connection, owner, values["name"])
            ):
                raise PlaylistError("You already have a playlist with that name.")
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
