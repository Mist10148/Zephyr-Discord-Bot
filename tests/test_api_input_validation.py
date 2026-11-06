"""Malformed input is a 400 with the JSON envelope, never a 500.

Each case here used to escape validation and raise inside a view: Unicode
digits that pass str.isdigit() but not int(), JSON values of the wrong type,
and nulls that str() turned into the literal word "None".
"""

from unittest.mock import patch

import pytest


def _headers(logged_in):
    return {"X-Zephyr-CSRF": logged_in.csrf}


def _geocoded():
    return [{"name": "Iloilo City", "latitude": 10.72, "longitude": 122.56, "timezone": "Asia/Manila"}]


def _create_sub(client, logged_in, **overrides):
    body = {"kind": "daily", "location": "Iloilo", "channel_id": "5", "schedule_local_time": "08:00", **overrides}
    with patch("website.api.weather_subs.geocode_search", return_value=_geocoded()):
        return client.post("/api/v1/guilds/1/weather-subs", json=body, headers=_headers(logged_in))


def _create_playlist(client, logged_in, **overrides):
    body = {"name": "Weekend", "tracks": [{"title": "A", "url": "https://youtu.be/a"}], **overrides}
    return client.post("/api/v1/playlists", json=body, headers=_headers(logged_in))


class TestIds:
    def test_id_helpers_reject_unicode_digits_and_overflow(self):
        from website.api import is_row_id, is_snowflake

        assert is_snowflake("123456789012345678")
        assert not is_snowflake("²")
        assert not is_snowflake("1" * 21)
        assert is_row_id("42")
        assert not is_row_id("²")
        assert not is_row_id("99999999999999999999")

    def test_a_unicode_digit_channel_id_is_refused(self, client, logged_in, fake_redis):
        assert _create_sub(client, logged_in, channel_id="²").status_code == 400

    def test_unicode_digit_and_huge_playlist_ids_are_404(self, client, logged_in, fake_redis):
        assert client.get("/api/v1/playlists/²").status_code == 404
        assert client.get("/api/v1/playlists/99999999999999999999").status_code == 404


class TestWeatherSubs:
    @pytest.mark.parametrize("override", [{"units": []}, {"tz": 5}, {"kind": ["daily"]}, {"schedule_local_time": 8}])
    def test_wrongly_typed_fields_are_400(self, client, logged_in, fake_redis, override):
        assert _create_sub(client, logged_in, **override).status_code == 400

    def test_a_null_location_is_not_geocoded_as_the_word_none(self, client, logged_in, fake_redis):
        sub_id = _create_sub(client, logged_in).get_json()["id"]
        with patch("website.api.weather_subs.geocode_search") as geocode:
            response = client.patch(f"/api/v1/guilds/1/weather-subs/{sub_id}", json={"location": None},
                                    headers=_headers(logged_in))
        assert response.status_code == 400
        geocode.assert_not_called()


class TestPlaylists:
    def test_a_non_object_load_body_is_400(self, client, logged_in, fake_redis):
        playlist_id = _create_playlist(client, logged_in).get_json()["id"]
        response = client.post(f"/api/v1/playlists/{playlist_id}/load", json=[1], headers=_headers(logged_in))
        assert response.status_code == 400

    @pytest.mark.parametrize("body", [{"name": 123}, {"is_public": "yes"}])
    def test_wrongly_typed_patch_fields_are_400(self, client, logged_in, fake_redis, body):
        playlist_id = _create_playlist(client, logged_in).get_json()["id"]
        response = client.patch(f"/api/v1/playlists/{playlist_id}", json=body, headers=_headers(logged_in))
        assert response.status_code == 400

    def test_a_failed_rename_leaves_the_tracks_alone(self, client, logged_in, fake_redis):
        """Both halves of a PATCH succeed or neither does."""
        _create_playlist(client, logged_in, name="Taken")
        playlist_id = _create_playlist(client, logged_in, name="Mine").get_json()["id"]

        response = client.patch(
            f"/api/v1/playlists/{playlist_id}",
            json={"name": "Taken", "tracks": [{"title": "Z", "url": "https://youtu.be/z"}]},
            headers=_headers(logged_in),
        )

        assert response.status_code == 400
        tracks = client.get(f"/api/v1/playlists/{playlist_id}").get_json()["tracks"]
        assert [track["title"] for track in tracks] == ["A"]


class TestSettings:
    @pytest.mark.parametrize("field", ["prefix", "locale", "timezone"])
    def test_null_is_refused_rather_than_saved_as_none(self, client, logged_in, fake_redis, field):
        response = client.patch("/api/v1/guilds/1/settings", json={field: None}, headers=_headers(logged_in))
        assert response.status_code == 400


class TestUnhandledErrors:
    def test_an_unexpected_failure_keeps_the_json_envelope(self, client, logged_in, fake_redis, monkeypatch):
        def boom(*_args, **_kwargs):
            raise RuntimeError("database is down")

        monkeypatch.setattr("website.api.playlists.repo.list_playlists", boom)

        response = client.get("/api/v1/playlists")

        assert response.status_code == 500
        assert response.get_json()["error"]["code"] == "internal_error"
