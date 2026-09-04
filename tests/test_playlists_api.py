"""Playlist CRUD, the Spotify importer, and editable guild settings."""

import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from zephyr.db.models import AuditLog
from zephyr.db.session import get_engine
from zephyr.services import bridge


def _headers(logged_in):
    return {"X-Zephyr-CSRF": logged_in.csrf}


def _create(client, logged_in, **overrides):
    body = {"name": "Weekend", "tracks": [{"title": "A", "url": "https://youtu.be/a"}], **overrides}
    return client.post("/api/v1/playlists", json=body, headers=_headers(logged_in))


class TestPlaylistCrud:
    def test_create_list_and_read_back(self, client, logged_in, fake_redis):
        created = _create(client, logged_in)
        assert created.status_code == 201
        playlist_id = created.get_json()["id"]

        listed = client.get("/api/v1/playlists").get_json()["playlists"]
        assert [row["name"] for row in listed] == ["Weekend"]
        assert listed[0]["mine"] is True

        detail = client.get(f"/api/v1/playlists/{playlist_id}").get_json()
        assert [track["title"] for track in detail["tracks"]] == ["A"]

    def test_a_playlist_needs_at_least_one_track(self, client, logged_in, fake_redis):
        response = _create(client, logged_in, tracks=[])
        assert response.status_code == 400

    def test_a_track_url_must_be_a_link(self, client, logged_in, fake_redis):
        response = _create(client, logged_in, tracks=[{"title": "A", "url": "javascript:alert(1)"}])
        assert response.status_code == 400

    def test_a_track_may_have_no_url_at_all(self, client, logged_in, fake_redis):
        """Imported tracks are titles until they are played."""
        response = _create(client, logged_in, tracks=[{"title": "Artist - Song", "source": "spotify"}])
        assert response.status_code == 201

    def test_reordering_is_a_whole_list_rewrite(self, client, logged_in, fake_redis):
        playlist_id = _create(
            client, logged_in,
            tracks=[{"title": "A", "url": "https://youtu.be/a"}, {"title": "B", "url": "https://youtu.be/b"}],
        ).get_json()["id"]

        response = client.patch(
            f"/api/v1/playlists/{playlist_id}",
            json={"tracks": [{"title": "B", "url": "https://youtu.be/b"}, {"title": "A", "url": "https://youtu.be/a"}]},
            headers=_headers(logged_in),
        )

        assert [track["title"] for track in response.get_json()["tracks"]] == ["B", "A"]

    def test_rename_and_publish(self, client, logged_in, fake_redis):
        playlist_id = _create(client, logged_in).get_json()["id"]

        response = client.patch(
            f"/api/v1/playlists/{playlist_id}",
            json={"name": "Sunday", "is_public": True},
            headers=_headers(logged_in),
        )

        assert response.get_json()["name"] == "Sunday"
        assert response.get_json()["is_public"] is True

    def test_unknown_fields_are_rejected_rather_than_dropped(self, client, logged_in, fake_redis):
        playlist_id = _create(client, logged_in).get_json()["id"]
        response = client.patch(
            f"/api/v1/playlists/{playlist_id}", json={"owner_id": "999"}, headers=_headers(logged_in)
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "unknown_fields"

    def test_delete_removes_it(self, client, logged_in, fake_redis):
        playlist_id = _create(client, logged_in).get_json()["id"]

        assert client.delete(f"/api/v1/playlists/{playlist_id}", headers=_headers(logged_in)).status_code == 204
        assert client.get(f"/api/v1/playlists/{playlist_id}").status_code == 404

    def test_somebody_elses_private_playlist_is_404_not_403(self, app, client, logged_in, fake_redis):
        """Whether a playlist id exists is not something a stranger may probe."""
        from zephyr.db.playlists import save_playlist

        other = save_playlist("999", "Theirs", [{"title": "A"}], database_url=app.config["DATABASE_URL"])

        assert client.get(f"/api/v1/playlists/{other['id']}").status_code == 404
        assert client.delete(f"/api/v1/playlists/{other['id']}", headers=_headers(logged_in)).status_code == 404

    def test_a_public_playlist_in_your_guild_is_readable(self, app, client, logged_in, fake_redis):
        from zephyr.db.playlists import save_playlist

        theirs = save_playlist(
            "999", "Shared", [{"title": "A"}], guild_id="1", is_public=True,
            database_url=app.config["DATABASE_URL"],
        )

        body = client.get(f"/api/v1/playlists/{theirs['id']}").get_json()
        assert body["mine"] is False

    def test_signing_out_is_a_401(self, client, fake_redis):
        assert client.get("/api/v1/playlists").status_code == 401


class TestSpotifyImport:
    def _spotify(self, monkeypatch, *, tracks=None, name="Discover Weekly"):
        client = MagicMock()
        monkeypatch.setattr("zephyr.services.spotify.build_client", lambda *a, **k: client)
        monkeypatch.setattr(
            "zephyr.services.spotify.fetch_playlist_metadata",
            lambda _client, _url, **kwargs: (name, tracks if tracks is not None else [
                {"title": "Artist - One", "url": None, "duration_s": 200, "source": "spotify"},
                {"title": "Artist - Two", "url": None, "duration_s": 180, "source": "spotify"},
            ]),
        )
        return client

    def test_an_import_stores_titles_with_no_urls(self, app, client, logged_in, fake_redis, monkeypatch):
        app.config["SPOTIFY_CLIENT_ID"] = "id"
        app.config["SPOTIFY_CLIENT_SECRET"] = "secret"
        self._spotify(monkeypatch)

        response = client.post(
            "/api/v1/playlists/import/spotify",
            json={"url": "https://open.spotify.com/playlist/abc"},
            headers=_headers(logged_in),
        )

        assert response.status_code == 201
        playlist_id = response.get_json()["id"]
        tracks = client.get(f"/api/v1/playlists/{playlist_id}").get_json()["tracks"]
        assert [track["url"] for track in tracks] == [None, None]
        assert response.get_json()["name"] == "Discover Weekly"

    def test_a_deployment_without_credentials_says_so(self, app, client, logged_in, fake_redis):
        app.config["SPOTIFY_CLIENT_ID"] = None
        app.config["SPOTIFY_CLIENT_SECRET"] = None

        response = client.post(
            "/api/v1/playlists/import/spotify",
            json={"url": "https://open.spotify.com/playlist/abc"},
            headers=_headers(logged_in),
        )

        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "spotify_not_configured"

    def test_a_link_that_is_not_spotify_is_a_400(self, app, client, logged_in, fake_redis, monkeypatch):
        app.config["SPOTIFY_CLIENT_ID"] = "id"
        app.config["SPOTIFY_CLIENT_SECRET"] = "secret"
        monkeypatch.setattr("zephyr.services.spotify.build_client", lambda *a, **k: MagicMock())
        monkeypatch.setattr(
            "zephyr.services.spotify.fetch_playlist_metadata",
            lambda *a, **k: (_ for _ in ()).throw(ValueError("That is not a Spotify link.")),
        )

        response = client.post(
            "/api/v1/playlists/import/spotify",
            json={"url": "https://example.com"},
            headers=_headers(logged_in),
        )

        assert response.status_code == 400

    def test_spotify_being_down_is_502_not_500(self, app, client, logged_in, fake_redis, monkeypatch):
        app.config["SPOTIFY_CLIENT_ID"] = "id"
        app.config["SPOTIFY_CLIENT_SECRET"] = "secret"
        monkeypatch.setattr("zephyr.services.spotify.build_client", lambda *a, **k: MagicMock())
        monkeypatch.setattr(
            "zephyr.services.spotify.fetch_playlist_metadata",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("503 from Spotify")),
        )

        response = client.post(
            "/api/v1/playlists/import/spotify",
            json={"url": "https://open.spotify.com/playlist/abc"},
            headers=_headers(logged_in),
        )

        assert response.status_code == 502


class TestLoadIntoGuild:
    def test_loading_goes_through_the_bot(self, client, logged_in, fake_redis):
        playlist_id = _create(client, logged_in).get_json()["id"]
        seen = {}

        def responder(channel, raw):
            if channel == bridge.COMMAND_CHANNEL:
                seen.update(json.loads(raw))
                bridge.publish_response(seen["id"], ok=True, data={"added": 1, "name": "Weekend"})

        fake_redis.on_publish = responder
        response = client.post(
            f"/api/v1/playlists/{playlist_id}/load", json={"guild_id": "1"}, headers=_headers(logged_in)
        )

        assert response.status_code == 200
        assert seen["action"] == "playlist.load"
        assert seen["args"]["playlist_id"] == playlist_id

    def test_a_guild_you_do_not_manage_is_forbidden(self, client, logged_in, fake_redis):
        playlist_id = _create(client, logged_in).get_json()["id"]
        response = client.post(
            f"/api/v1/playlists/{playlist_id}/load", json={"guild_id": "999"}, headers=_headers(logged_in)
        )
        assert response.status_code == 403


class TestGuildSettings:
    def test_a_patch_is_stored_and_read_back(self, client, logged_in, fake_redis):
        response = client.patch(
            "/api/v1/guilds/1/settings",
            json={"prefix": "!", "timezone": "Asia/Manila", "default_volume": 70},
            headers=_headers(logged_in),
        )

        assert response.status_code == 200
        assert response.get_json()["prefix"] == "!"
        assert response.get_json()["defaults_applied"] is False
        assert client.get("/api/v1/guilds/1/settings").get_json()["timezone"] == "Asia/Manila"

    def test_a_partial_patch_does_not_blank_the_rest(self, client, logged_in, fake_redis):
        client.patch("/api/v1/guilds/1/settings", json={"prefix": "!", "locale": "fil"},
                     headers=_headers(logged_in))
        client.patch("/api/v1/guilds/1/settings", json={"prefix": "?"}, headers=_headers(logged_in))

        assert client.get("/api/v1/guilds/1/settings").get_json()["locale"] == "fil"

    @pytest.mark.parametrize(
        "body",
        [
            {"prefix": ""},
            {"prefix": "far too long"},
            {"timezone": "Middle/Earth"},
            {"default_volume": 5000},
            {"default_volume": "loud"},
            {"dj_role_id": "not-an-id"},
            {"music_channel_ids": "5"},
            # bool("false") is True, so a string here would enable the DJ lock
            # for a client that meant to disable it.
            {"dj_only": "false"},
            {"dj_only": 1},
            {"always_on": "true"},
            {"vote_skip_ratio": 2},
            {"vote_skip_ratio": 0},
            {"vote_skip_ratio": "half"},
        ],
    )
    def test_invalid_values_are_refused(self, client, logged_in, fake_redis, body):
        response = client.patch("/api/v1/guilds/1/settings", json=body, headers=_headers(logged_in))
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_value"

    def test_the_player_policy_round_trips(self, client, logged_in, fake_redis):
        response = client.patch(
            "/api/v1/guilds/1/settings",
            json={"dj_only": True, "vote_skip_ratio": 0.75},
            headers=_headers(logged_in),
        )

        assert response.status_code == 200
        settings = client.get("/api/v1/guilds/1/settings").get_json()
        assert settings["dj_only"] is True
        assert settings["vote_skip_ratio"] == 0.75

    def test_the_dj_lock_defaults_to_off(self, client, logged_in, fake_redis):
        """On by default would silently take the player away from everybody who
        could use it yesterday."""
        settings = client.get("/api/v1/guilds/1/settings").get_json()

        assert settings["dj_only"] is False
        assert settings["vote_skip_ratio"] == 0.5

    def test_enabled_cogs_is_not_writable_even_though_it_is_returned(self, client, logged_in, fake_redis):
        """It comes from deployment configuration, not from the dashboard."""
        response = client.patch(
            "/api/v1/guilds/1/settings", json={"enabled_cogs": ["music"]}, headers=_headers(logged_in)
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "unknown_fields"

    def test_a_guild_you_do_not_manage_is_forbidden(self, client, logged_in, fake_redis):
        response = client.patch(
            "/api/v1/guilds/999/settings", json={"prefix": "!"}, headers=_headers(logged_in)
        )
        assert response.status_code == 403

    def test_a_missing_csrf_token_is_rejected(self, client, logged_in, fake_redis):
        assert client.patch("/api/v1/guilds/1/settings", json={"prefix": "!"}).status_code == 403

    def test_a_change_is_audited(self, app, client, logged_in, fake_redis):
        client.patch("/api/v1/guilds/1/settings", json={"prefix": "!"}, headers=_headers(logged_in))

        with get_engine(app.config["DATABASE_URL"]).connect() as connection:
            rows = connection.execute(select(AuditLog.action, AuditLog.payload, AuditLog.source)).all()
        assert rows[0].action == "settings.update"
        assert rows[0].payload == {"prefix": "!"}
        assert rows[0].source == "web"

    def test_setting_the_dj_role_tells_the_bot_to_reload(self, client, logged_in, fake_redis):
        """The bot caches it for its permission check; a silent save would leave
        the wrong role enforced until the next slow refresh."""
        seen = []

        def responder(channel, raw):
            if channel == bridge.COMMAND_CHANNEL:
                command = json.loads(raw)
                seen.append(command["action"])
                bridge.publish_response(command["id"], ok=True, data={})

        fake_redis.on_publish = responder
        client.patch(
            "/api/v1/guilds/1/settings", json={"dj_role_id": "777"}, headers=_headers(logged_in)
        )

        assert seen == ["settings.reload"]

    def test_an_unreachable_bot_does_not_fail_the_save(self, client, logged_in, fake_redis, monkeypatch):
        monkeypatch.setattr(bridge, "COMMAND_TIMEOUT", 0.05)

        response = client.patch(
            "/api/v1/guilds/1/settings", json={"dj_role_id": "777"}, headers=_headers(logged_in)
        )

        assert response.status_code == 200
        assert client.get("/api/v1/guilds/1/settings").get_json()["dj_role_id"] == "777"
