"""Playlist CRUD and the Spotify importer.

Playlists belong to a user, not a guild, so these endpoints are scoped by
session rather than by ``guild_scoped``.  A playlist is visible to its owner
always, and to anyone in the guild it was saved in when it is public.
"""

from flask import current_app, g, jsonify, request

from website.api import api, error
from website.api.guard import current_session, require_session
from zephyr.db import playlists as repo
from zephyr.services import spotify

MAX_TITLE_LENGTH = 300


def _database_url():
    return current_app.config["DATABASE_URL"]


def _owned(playlist_id: str):
    """Load a playlist and confirm the session owns it.

    Returns ``(playlist, None)`` or ``(None, response)``.  404 for "not yours"
    as well as "not there": whether a playlist id exists is not something a
    stranger should be able to probe.
    """
    if not str(playlist_id).isdigit():
        return None, error("not_found", "No such playlist.", 404)
    playlist = repo.get_playlist(int(playlist_id), database_url=_database_url())
    if playlist is None or playlist["owner_id"] != current_session().user_id:
        return None, error("not_found", "No such playlist.", 404)
    return playlist, None


def _clean_tracks(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("tracks must be a list.")
    if len(raw) > repo.MAX_TRACKS:
        raise ValueError(f"At most {repo.MAX_TRACKS} tracks.")
    tracks = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("Each track must be an object.")
        title = str(entry.get("title") or "")[:MAX_TITLE_LENGTH]
        url = entry.get("url")
        if url is not None and not str(url).startswith(("http://", "https://")):
            raise ValueError("A track url must be an http(s) link.")
        tracks.append(
            {
                "title": title,
                "url": str(url) if url else None,
                "duration_s": entry.get("duration_s") or 0,
                "source": str(entry.get("source") or "youtube")[:32],
            }
        )
    return tracks


@api.get("/playlists")
@require_session
def list_playlists():
    guild_id = request.args.get("guild_id")
    if guild_id and not guild_id.isdigit():
        return error("invalid_guild_id", "That is not a Discord guild id.", 400)
    session = current_session()
    rows = repo.list_playlists(session.user_id, guild_id=guild_id, database_url=_database_url())
    return jsonify(
        {
            "playlists": [
                {**row, "id": row["id"], "mine": row["owner_id"] == session.user_id,
                 "created_at": row["created_at"].isoformat() if row.get("created_at") else None}
                for row in rows
            ]
        }
    )


@api.post("/playlists")
@require_session
def create_playlist():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error("invalid_body", "Send a JSON object.", 400)
    try:
        tracks = _clean_tracks(body.get("tracks") or [])
    except ValueError as exc:
        return error("invalid_value", str(exc), 400)
    if not tracks:
        return error("invalid_value", "A playlist needs at least one track.", 400)

    guild_id = body.get("guild_id")
    if guild_id is not None and not str(guild_id).isdigit():
        return error("invalid_guild_id", "That is not a Discord guild id.", 400)
    try:
        saved = repo.save_playlist(
            current_session().user_id,
            str(body.get("name") or ""),
            tracks,
            guild_id=str(guild_id) if guild_id else None,
            is_public=bool(body.get("is_public")),
            database_url=_database_url(),
        )
    except repo.PlaylistError as exc:
        return error("invalid_value", str(exc), 400)
    return jsonify(saved), 201


@api.get("/playlists/<playlist_id>")
@require_session
def get_playlist(playlist_id: str):
    if not str(playlist_id).isdigit():
        return error("not_found", "No such playlist.", 404)
    playlist = repo.get_playlist(int(playlist_id), database_url=_database_url())
    session = current_session()
    # A public playlist is readable by anyone who shares its guild -- which the
    # session's own guild list is enough to establish.
    if playlist is None or (
        playlist["owner_id"] != session.user_id
        and not (playlist["is_public"] and str(playlist["guild_id"] or "") in session.manageable_ids())
    ):
        return error("not_found", "No such playlist.", 404)
    return jsonify({**playlist, "mine": playlist["owner_id"] == session.user_id,
                    "created_at": playlist["created_at"].isoformat() if playlist.get("created_at") else None})


@api.patch("/playlists/<playlist_id>")
@require_session
def patch_playlist(playlist_id: str):
    playlist, failure = _owned(playlist_id)
    if failure:
        return failure
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error("invalid_body", "Send a JSON object.", 400)

    unknown = sorted(set(body) - {"name", "is_public", "tracks"})
    if unknown:
        return error("unknown_fields", f"Cannot set: {', '.join(unknown)}.", 400, unknown)

    try:
        if "tracks" in body:
            # A whole-list rewrite, which is also how a reorder arrives: position
            # is half the primary key, so there is no "move one row" to express.
            repo.replace_tracks(playlist["id"], _clean_tracks(body["tracks"]), database_url=_database_url())
        if "name" in body or "is_public" in body:
            repo.update_playlist(
                playlist["id"],
                name=body.get("name"),
                is_public=body.get("is_public"),
                database_url=_database_url(),
            )
    except (ValueError, repo.PlaylistError) as exc:
        return error("invalid_value", str(exc), 400)

    updated = repo.get_playlist(playlist["id"], database_url=_database_url())
    return jsonify({**updated, "mine": True,
                    "created_at": updated["created_at"].isoformat() if updated.get("created_at") else None})


@api.delete("/playlists/<playlist_id>")
@require_session
def delete_playlist(playlist_id: str):
    playlist, failure = _owned(playlist_id)
    if failure:
        return failure
    repo.delete_playlist(playlist["id"], database_url=_database_url())
    return "", 204


@api.post("/playlists/import/spotify")
@require_session
def import_spotify():
    """Import a Spotify track, album or playlist as metadata only.

    No yt-dlp and no YouTube lookups: a 200-track playlist is two Spotify calls
    and finishes inside a request, and the rows it writes carry no URL.  Each
    track is resolved once, at play time, and only if it is actually played --
    which is also why a saved playlist keeps working after the video it was
    originally saved from is taken down.
    """
    client = spotify.build_client(
        current_app.config["SPOTIFY_CLIENT_ID"], current_app.config["SPOTIFY_CLIENT_SECRET"]
    )
    if client is None:
        return error(
            "spotify_not_configured",
            "This deployment has no Spotify credentials, so importing is unavailable.",
            503,
        )

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error("invalid_body", "Send a JSON object.", 400)
    url = str(body.get("url") or "").strip()
    if not url:
        return error("invalid_value", "Send the Spotify link to import.", 400)

    try:
        name, tracks = spotify.fetch_playlist_metadata(client, url)
    except ValueError as exc:
        return error("invalid_value", str(exc), 400)
    except Exception as exc:
        print(f"[Playlists] Spotify import failed: {exc}")
        return error("spotify_unavailable", "Spotify did not answer.", 502)

    if not tracks:
        return error("invalid_value", "That Spotify link has no tracks.", 400)

    guild_id = body.get("guild_id")
    if guild_id is not None and not str(guild_id).isdigit():
        return error("invalid_guild_id", "That is not a Discord guild id.", 400)
    try:
        saved = repo.save_playlist(
            current_session().user_id,
            str(body.get("name") or name),
            tracks,
            guild_id=str(guild_id) if guild_id else None,
            is_public=bool(body.get("is_public")),
            database_url=_database_url(),
        )
    except repo.PlaylistError as exc:
        return error("invalid_value", str(exc), 400)
    return jsonify({**saved, "source": "spotify"}), 201


@api.post("/playlists/<playlist_id>/load")
@require_session
def load_playlist(playlist_id: str):
    """Queue a playlist up in a guild, via the bot."""
    from website.api.player import bridge_call

    if not str(playlist_id).isdigit():
        return error("not_found", "No such playlist.", 404)
    body = request.get_json(silent=True) or {}
    guild_id = str(body.get("guild_id") or "")
    if not guild_id.isdigit():
        return error("invalid_guild_id", "Send the guild to load it into.", 400)
    if guild_id not in current_session().manageable_ids():
        return error("forbidden", "You do not manage that server.", 403)

    return bridge_call(
        "playlist.load",
        guild_id=guild_id,
        actor_id=g.zephyr_session.user_id,
        args={"playlist_id": int(playlist_id)},
    )
