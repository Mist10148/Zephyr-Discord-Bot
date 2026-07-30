"""Weather subscription CRUD and the preview endpoint."""

from unittest.mock import patch

import pytest


def _headers(logged_in):
    return {"X-Zephyr-CSRF": logged_in.csrf}


def _geocoded(name="Iloilo City"):
    return [{"name": name, "latitude": 10.72, "longitude": 122.56, "timezone": "Asia/Manila",
             "country": "Philippines", "admin1": "Western Visayas"}]


def _bundle(*, wind=10, rain=10, feels=25, code=0, feels_max=30):
    times = [f"2026-07-30T{9 + index:02d}:00" for index in range(3)]
    return {
        "current": {"time": "2026-07-30T09:00", "temperature_2m": 28, "apparent_temperature": feels, "weather_code": code},
        "daily": {"time": ["2026-07-30"], "weather_code": [code], "temperature_2m_max": [33],
                  "temperature_2m_min": [25], "apparent_temperature_max": [feels_max],
                  "apparent_temperature_min": [26], "precipitation_probability_max": [rain],
                  "wind_speed_10m_max": [wind]},
        "hourly": {"time": times, "wind_speed_10m": [wind] * 3, "precipitation_probability": [rain] * 3,
                   "apparent_temperature": [feels] * 3, "weather_code": [code] * 3},
    }


def _create(client, logged_in, **overrides):
    body = {"kind": "daily", "location": "Iloilo", "channel_id": "5", "schedule_local_time": "08:00", **overrides}
    with patch("website.api.weather_subs.geocode_search", return_value=_geocoded()):
        return client.post("/api/v1/guilds/1/weather-subs", json=body, headers=_headers(logged_in))


class TestCreate:
    def test_a_subscription_stores_what_the_geocoder_resolved(self, client, logged_in, fake_redis):
        response = _create(client, logged_in, location="iloilo")

        assert response.status_code == 201
        body = response.get_json()
        assert body["location"] == "Iloilo City"
        assert body["lat"] == 10.72
        # The geocoder's zone is the sensible default for a place's own digest.
        assert body["tz"] == "Asia/Manila"

    def test_a_daily_digest_without_a_time_is_refused(self, client, logged_in, fake_redis):
        """It would never fire, and a subscription that silently never fires is
        worse than one that refuses to be created."""
        response = _create(client, logged_in, schedule_local_time=None)
        assert response.status_code == 400

    def test_a_severe_watch_gets_the_default_thresholds(self, client, logged_in, fake_redis):
        response = _create(client, logged_in, kind="severe", schedule_local_time=None)

        assert response.status_code == 201
        assert response.get_json()["thresholds"]["wind_speed"] == 60.0

    @pytest.mark.parametrize(
        "body",
        [
            {"kind": "hurricane"},
            {"channel_id": "not-an-id"},
            {"tz": "Middle/Earth"},
            {"schedule_local_time": "breakfast"},
            {"units": "kelvin"},
        ],
    )
    def test_invalid_values_are_refused(self, client, logged_in, fake_redis, body):
        response = _create(client, logged_in, **body)
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_value"

    def test_unknown_fields_are_rejected_rather_than_dropped(self, client, logged_in, fake_redis):
        response = _create(client, logged_in, guild_id="999")
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "unknown_fields"

    def test_an_unfindable_place_is_a_400(self, client, logged_in, fake_redis):
        with patch("website.api.weather_subs.geocode_search", return_value=[]):
            response = client.post(
                "/api/v1/guilds/1/weather-subs",
                json={"kind": "daily", "location": "Atlantis", "channel_id": "5", "schedule_local_time": "08:00"},
                headers=_headers(logged_in),
            )
        assert response.status_code == 400

    def test_a_geocoder_outage_is_502(self, client, logged_in, fake_redis):
        from zephyr.utils.weather_utils import WeatherUpstreamError

        with patch("website.api.weather_subs.geocode_search", side_effect=WeatherUpstreamError("down")):
            response = client.post(
                "/api/v1/guilds/1/weather-subs",
                json={"kind": "daily", "location": "Iloilo", "channel_id": "5", "schedule_local_time": "08:00"},
                headers=_headers(logged_in),
            )
        assert response.status_code == 502

    def test_a_guild_you_do_not_manage_is_forbidden(self, client, logged_in, fake_redis):
        with patch("website.api.weather_subs.geocode_search", return_value=_geocoded()):
            response = client.post(
                "/api/v1/guilds/999/weather-subs",
                json={"kind": "daily", "location": "Iloilo", "channel_id": "5", "schedule_local_time": "08:00"},
                headers=_headers(logged_in),
            )
        assert response.status_code == 403

    def test_a_missing_csrf_token_is_rejected(self, client, logged_in, fake_redis):
        assert client.post("/api/v1/guilds/1/weather-subs", json={}).status_code == 403


class TestListAndEdit:
    def test_listing_includes_the_kinds_and_default_thresholds(self, client, logged_in, fake_redis):
        _create(client, logged_in)
        body = client.get("/api/v1/guilds/1/weather-subs").get_json()

        assert len(body["subscriptions"]) == 1
        assert "severe" in body["kinds"]
        assert body["default_thresholds"]["wind_speed"] == 60.0

    def test_a_patch_can_disable_without_touching_anything_else(self, client, logged_in, fake_redis):
        sub_id = _create(client, logged_in).get_json()["id"]

        response = client.patch(f"/api/v1/guilds/1/weather-subs/{sub_id}",
                                json={"enabled": False}, headers=_headers(logged_in))

        assert response.get_json()["enabled"] is False
        assert response.get_json()["schedule_local_time"] == "08:00"

    def test_switching_to_daily_without_a_time_is_refused(self, client, logged_in, fake_redis):
        """Checked against the merged row, not the patch alone."""
        sub_id = _create(client, logged_in, kind="severe", schedule_local_time=None).get_json()["id"]

        response = client.patch(f"/api/v1/guilds/1/weather-subs/{sub_id}",
                                json={"kind": "daily"}, headers=_headers(logged_in))

        assert response.status_code == 400

    def test_thresholds_may_be_narrowed_and_individually_disabled(self, client, logged_in, fake_redis):
        sub_id = _create(client, logged_in, kind="severe", schedule_local_time=None).get_json()["id"]

        response = client.patch(
            f"/api/v1/guilds/1/weather-subs/{sub_id}",
            json={"thresholds": {"wind_speed": 40, "precipitation_probability": None, "storm": False}},
            headers=_headers(logged_in),
        )

        thresholds = response.get_json()["thresholds"]
        assert thresholds["wind_speed"] == 40
        assert thresholds["precipitation_probability"] is None
        assert thresholds["storm"] is False

    def test_an_unknown_threshold_is_refused(self, client, logged_in, fake_redis):
        sub_id = _create(client, logged_in).get_json()["id"]
        response = client.patch(f"/api/v1/guilds/1/weather-subs/{sub_id}",
                                json={"thresholds": {"locusts": 1}}, headers=_headers(logged_in))
        assert response.status_code == 400

    def test_another_guilds_subscription_is_404(self, app, client, logged_in, fake_redis):
        """Ids are sequential across the database, so a guess must not reach one."""
        from zephyr.db.weather_subs import create

        theirs = create(
            {"guild_id": "999", "channel_id": "5", "kind": "daily", "location": "X", "lat": 1.0,
             "lon": 2.0, "units": "metric", "schedule_local_time": "08:00", "tz": "UTC",
             "thresholds": None, "enabled": True},
            database_url=app.config["DATABASE_URL"],
        )

        assert client.get(f"/api/v1/guilds/1/weather-subs/{theirs['id']}/preview").status_code == 404
        assert client.delete(f"/api/v1/guilds/1/weather-subs/{theirs['id']}",
                             headers=_headers(logged_in)).status_code == 404

    def test_delete_removes_it(self, client, logged_in, fake_redis):
        sub_id = _create(client, logged_in).get_json()["id"]

        assert client.delete(f"/api/v1/guilds/1/weather-subs/{sub_id}",
                             headers=_headers(logged_in)).status_code == 204
        assert client.get("/api/v1/guilds/1/weather-subs").get_json()["subscriptions"] == []

    def test_changes_are_audited(self, app, client, logged_in, fake_redis):
        from sqlalchemy import select

        from zephyr.db.models import AuditLog
        from zephyr.db.session import get_engine

        sub_id = _create(client, logged_in).get_json()["id"]
        client.patch(f"/api/v1/guilds/1/weather-subs/{sub_id}", json={"enabled": False}, headers=_headers(logged_in))
        client.delete(f"/api/v1/guilds/1/weather-subs/{sub_id}", headers=_headers(logged_in))

        with get_engine(app.config["DATABASE_URL"]).connect() as connection:
            actions = connection.execute(select(AuditLog.action)).scalars().all()
        assert actions == ["weather_sub.create", "weather_sub.update", "weather_sub.delete"]


class TestPreview:
    def test_a_preview_uses_the_same_evaluator_the_scheduler_does(self, client, logged_in, fake_redis):
        sub_id = _create(client, logged_in).get_json()["id"]

        with patch("website.api.weather_subs.get_openmeteo_bundle", return_value=_bundle(wind=40, rain=60)):
            body = client.get(f"/api/v1/guilds/1/weather-subs/{sub_id}/preview").get_json()

        assert body["would_post"] is True
        assert "Iloilo City" in body["alert"]["title"]
        fields = {field["name"]: field["value"] for field in body["alert"]["fields"]}
        assert fields["Chance of rain"] == "60%"

    def test_a_quiet_watch_previews_as_nothing_to_post(self, client, logged_in, fake_redis):
        """The most common case for a watch, and the UI has to be able to say so."""
        sub_id = _create(client, logged_in, kind="severe", schedule_local_time=None).get_json()["id"]

        with patch("website.api.weather_subs.get_openmeteo_bundle", return_value=_bundle()):
            body = client.get(f"/api/v1/guilds/1/weather-subs/{sub_id}/preview").get_json()

        assert body["would_post"] is False
        assert body["alert"] is None

    def test_a_preview_reports_when_it_would_be_deduplicated(self, app, client, logged_in, fake_redis):
        from zephyr.db.weather_subs import mark_fired
        from zephyr.utils.weather_alerts import evaluate_severe

        sub_id = _create(client, logged_in, kind="severe", schedule_local_time=None).get_json()["id"]
        stormy = _bundle(wind=90)
        fingerprint = evaluate_severe(stormy, None, location="Iloilo City")["fingerprint"]
        mark_fired(sub_id, fingerprint=fingerprint, database_url=app.config["DATABASE_URL"])

        with patch("website.api.weather_subs.get_openmeteo_bundle", return_value=stormy):
            body = client.get(f"/api/v1/guilds/1/weather-subs/{sub_id}/preview").get_json()

        assert body["would_post"] is True
        assert body["duplicate"] is True

    def test_a_provider_outage_is_502(self, client, logged_in, fake_redis):
        from zephyr.utils.weather_utils import WeatherUpstreamError

        sub_id = _create(client, logged_in).get_json()["id"]
        with patch("website.api.weather_subs.get_openmeteo_bundle", side_effect=WeatherUpstreamError("down")):
            response = client.get(f"/api/v1/guilds/1/weather-subs/{sub_id}/preview")
        assert response.status_code == 502

    def test_signing_out_is_a_401(self, client, fake_redis):
        assert client.get("/api/v1/guilds/1/weather-subs").status_code == 401
