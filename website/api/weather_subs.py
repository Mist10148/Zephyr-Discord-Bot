"""Weather subscription CRUD, plus a preview.

The preview calls the same pure evaluators the bot's scheduler does
(``zephyr/utils/weather_alerts.py``), so what somebody sees before saving is what
the channel receives afterwards.  Rendering it separately here would have been
easier and wrong.
"""

from flask import current_app, g, jsonify, request

from website.api import api, error, is_row_id, is_snowflake
from website.api.cache import TTLCache
from website.api.guard import guild_scoped
from zephyr.db import audit
from zephyr.db import weather_subs as repo
from zephyr.utils import weather_alerts
from zephyr.utils.weather_utils import WeatherProviderError, geocode_search, get_openmeteo_bundle

# Shared with the public weather endpoint's reasoning: a forecast bundle is worth
# reusing for a few minutes, and a preview is often reloaded while somebody
# fiddles with thresholds.
cache = TTLCache()

CREATE_REQUIRED = {"kind", "location", "channel_id"}
EDITABLE = {"channel_id", "kind", "location", "schedule_local_time", "tz", "thresholds", "enabled", "units"}


def _database_url():
    return current_app.config["DATABASE_URL"]


def _public(row: dict) -> dict:
    return {
        **row,
        "id": row["id"],
        "last_run_at": row["last_run_at"].isoformat() if row.get("last_run_at") else None,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "kind_label": {"daily": "Daily digest", "severe": "Severe weather watch",
                       "class_suspension": "Class suspension watch"}.get(row["kind"], row["kind"]),
    }


def _clean_thresholds(value, units: str = "metric"):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("thresholds must be an object.")
    unknown = sorted(set(value) - set(weather_alerts.DEFAULT_THRESHOLDS))
    if unknown:
        raise ValueError(f"Unknown thresholds: {', '.join(unknown)}.")
    cleaned = {}
    for key, raw in value.items():
        if key == "storm":
            # bool("false") is True; only a real boolean is accepted.
            if not isinstance(raw, bool):
                raise ValueError("storm must be true or false.")
            cleaned[key] = raw
            continue
        if raw is None:
            # None disables one threshold without disabling the subscription --
            # "warn me about wind but never about rain" has to be expressible.
            cleaned[key] = None
            continue
        # float(True) is 1.0 (a 1% rain threshold), and NaN or Infinity pass
        # float() too -- a stored NaN then broke the dashboard's JSON.parse.
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise ValueError(f"{key} must be a number.")
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number.") from None
        if not weather_alerts.threshold_in_range(key, number, units):
            raise ValueError(f"{key} is out of range.")
        cleaned[key] = number
    return cleaned


def _clean_common(body: dict, *, partial: bool, current_units: str = "metric") -> dict:
    values = {}
    if "kind" in body:
        if not isinstance(body["kind"], str) or body["kind"] not in repo.KINDS:
            raise ValueError(f"kind must be one of {', '.join(repo.KINDS)}.")
        values["kind"] = body["kind"]
    if "channel_id" in body:
        if not is_snowflake(body["channel_id"]):
            raise ValueError("channel_id must be a Discord id.")
        values["channel_id"] = str(body["channel_id"])
    if "units" in body:
        if not isinstance(body["units"], str) or body["units"] not in {"metric", "imperial"}:
            raise ValueError("units must be metric or imperial.")
        values["units"] = body["units"]
    if "tz" in body:
        if not isinstance(body["tz"], str):
            raise ValueError("tz must be an IANA timezone name, like Asia/Manila.")
        zone_name, accepted = repo.normalise_zone(body["tz"])
        if not accepted:
            raise ValueError("tz must be an IANA timezone name, like Asia/Manila.")
        values["tz"] = zone_name
    if "schedule_local_time" in body:
        raw = body["schedule_local_time"]
        if raw is not None and not isinstance(raw, str):
            raise ValueError("schedule_local_time must look like 08:00.")
        values["schedule_local_time"] = repo.parse_local_time(raw).strftime("%H:%M") if raw else None
    if "thresholds" in body:
        values["thresholds"] = _clean_thresholds(body["thresholds"], values.get("units", current_units))
    if "enabled" in body:
        # bool("false") is True: a string would have switched the sub on.
        if not isinstance(body["enabled"], bool):
            raise ValueError("enabled must be true or false.")
        values["enabled"] = body["enabled"]

    # A daily digest with no time would never fire, and a subscription that
    # silently never fires is worse than one that refuses to be created.
    kind = values.get("kind")
    if kind == "daily" and not partial and not values.get("schedule_local_time"):
        raise ValueError("A daily digest needs a time, like 08:00.")
    return values


@api.get("/guilds/<guild_id>/weather-subs")
@guild_scoped
def list_subs(guild_id: str):
    rows = repo.list_for_guild(guild_id, database_url=_database_url())
    return jsonify({"subscriptions": [_public(row) for row in rows], "kinds": list(repo.KINDS),
                    "default_thresholds": weather_alerts.DEFAULT_THRESHOLDS})


@api.post("/guilds/<guild_id>/weather-subs")
@guild_scoped
def create_sub(guild_id: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error("invalid_body", "Send a JSON object.", 400)
    unknown = sorted(set(body) - EDITABLE)
    if unknown:
        return error("unknown_fields", f"Cannot set: {', '.join(unknown)}.", 400, unknown)
    missing = sorted(CREATE_REQUIRED - set(body))
    if missing:
        return error("invalid_body", f"Missing: {', '.join(missing)}.", 400, missing)

    try:
        values = _clean_common(body, partial=False)
    except (ValueError, repo.SubscriptionError) as exc:
        return error("invalid_value", str(exc), 400)

    location = body.get("location")
    location = location.strip() if isinstance(location, str) else ""
    if not location:
        return error("invalid_value", "Send a place to watch.", 400)
    try:
        places = geocode_search(location, 1)
    except WeatherProviderError as exc:
        return error("geocode_unavailable", str(exc), 502)
    if not places:
        return error("invalid_value", f"Could not find {location}.", 400)
    place = places[0]

    values.update(
        {
            "guild_id": guild_id,
            # What the geocoder resolved, not what was typed: the coordinates and
            # the name have to describe the same place.
            "location": place.get("name") or location,
            "lat": place["latitude"],
            "lon": place["longitude"],
        }
    )
    values.setdefault("units", "metric")
    values.setdefault("tz", place.get("timezone") or "UTC")
    values.setdefault("enabled", True)
    if values["kind"] == "severe":
        # In the subscription's own units: the metric defaults compared against
        # an imperial forecast meant "feels like 38 °F" and "60 mph wind".
        values.setdefault("thresholds", weather_alerts.thresholds_for(values["units"]))

    try:
        created = repo.create(values, database_url=_database_url())
    except repo.SubscriptionError as exc:
        return error("invalid_value", str(exc), 400)

    audit.record("weather_sub.create", actor_id=g.zephyr_session.user_id, guild_id=guild_id,
                 payload={"id": created["id"], "kind": created["kind"], "location": created["location"]},
                 source="web", database_url=_database_url())
    return jsonify(_public(created)), 201


def _owned(guild_id: str, sub_id: str):
    if not is_row_id(sub_id):
        return None, error("not_found", "No such subscription.", 404)
    row = repo.get(int(sub_id), database_url=_database_url())
    # Guild-scoped, because ids are sequential across the whole database and a
    # guess would otherwise reach another server's row.
    if row is None or row["guild_id"] != str(guild_id):
        return None, error("not_found", "No such subscription.", 404)
    return row, None


@api.patch("/guilds/<guild_id>/weather-subs/<sub_id>")
@guild_scoped
def patch_sub(guild_id: str, sub_id: str):
    row, failure = _owned(guild_id, sub_id)
    if failure:
        return failure
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error("invalid_body", "Send a JSON object.", 400)
    unknown = sorted(set(body) - EDITABLE)
    if unknown:
        return error("unknown_fields", f"Cannot set: {', '.join(unknown)}.", 400, unknown)

    try:
        values = _clean_common(body, partial=True, current_units=row.get("units") or "metric")
    except (ValueError, repo.SubscriptionError) as exc:
        return error("invalid_value", str(exc), 400)

    old_units = row.get("units") or "metric"
    if "units" in values and values["units"] != old_units and "thresholds" not in values and row.get("thresholds"):
        # Keep the same real-world limits in the new units.
        stored = row["thresholds"]
        if old_units == "imperial" and stored == weather_alerts.DEFAULT_THRESHOLDS:
            stored = weather_alerts.thresholds_for("imperial")
        values["thresholds"] = weather_alerts.convert_thresholds(stored, old_units, values["units"])

    if "location" in body:
        # str(None) would geocode the literal word "None".
        location = body["location"].strip() if isinstance(body["location"], str) else ""
        if not location:
            return error("invalid_value", "Send a place to watch.", 400)
        try:
            places = geocode_search(location, 1)
        except WeatherProviderError as exc:
            return error("geocode_unavailable", str(exc), 502)
        if not places:
            return error("invalid_value", f"Could not find {location}.", 400)
        values.update({"location": places[0].get("name") or location,
                       "lat": places[0]["latitude"], "lon": places[0]["longitude"]})
        if "tz" not in body:
            # As on create: the new place's own zone.  Keeping the old one made
            # an 08:00 Manila digest moved to London post at midnight there.
            values["tz"] = places[0].get("timezone") or row["tz"]

    # Checked against the merged result, not the patch: switching an existing
    # severe watch to a daily digest must not leave it without a time.
    merged_kind = values.get("kind", row["kind"])
    merged_time = values.get("schedule_local_time", row["schedule_local_time"])
    if merged_kind == "daily" and not merged_time:
        return error("invalid_value", "A daily digest needs a time, like 08:00.", 400)

    if not values:
        return error("invalid_body", "Nothing to change.", 400)
    updated = repo.update_sub(int(sub_id), values, database_url=_database_url())
    if updated is None:
        # Deleted between the ownership check and the write.
        return error("not_found", "No such subscription.", 404)
    audit.record("weather_sub.update", actor_id=g.zephyr_session.user_id, guild_id=guild_id,
                 payload={"id": int(sub_id), **{key: values[key] for key in sorted(values)}},
                 source="web", database_url=_database_url())
    return jsonify(_public(updated))


@api.delete("/guilds/<guild_id>/weather-subs/<sub_id>")
@guild_scoped
def delete_sub(guild_id: str, sub_id: str):
    _, failure = _owned(guild_id, sub_id)
    if failure:
        return failure
    repo.delete_sub(int(sub_id), database_url=_database_url())
    audit.record("weather_sub.delete", actor_id=g.zephyr_session.user_id, guild_id=guild_id,
                 payload={"id": int(sub_id)}, source="web", database_url=_database_url())
    return "", 204


@api.get("/guilds/<guild_id>/weather-subs/<sub_id>/preview")
@guild_scoped
def preview_sub(guild_id: str, sub_id: str):
    """Exactly what this subscription would post right now.

    ``alert: null`` is a real answer, not an empty one: a watch that would stay
    quiet is the most common case and the UI has to be able to say so.
    """
    row, failure = _owned(guild_id, sub_id)
    if failure:
        return failure
    units = row.get("units") or "metric"
    key = f"subpreview:{row['lat']:.2f}:{row['lon']:.2f}:{units}"
    try:
        bundle = cache.get_or_load(key, 300, lambda: get_openmeteo_bundle(row["lat"], row["lon"], units=units))
    except WeatherProviderError as exc:
        return error("weather_unavailable", str(exc), 502)

    try:
        alert = weather_alerts.evaluate(row["kind"], bundle, location=row["location"], units=units,
                                        thresholds=row.get("thresholds"))
    except (KeyError, TypeError, ValueError) as exc:
        # A malformed thresholds row or a partial bundle: answer in the API's
        # JSON error shape rather than Flask's HTML 500 page.
        return error("preview_failed", f"This subscription could not be evaluated ({exc.__class__.__name__}).", 500)
    return jsonify({"id": row["id"], "kind": row["kind"], "alert": alert,
                    "would_post": alert is not None,
                    # The scheduler's own rule.  Equality disagreed with it as
                    # soon as a stored fingerprint had been merged, or a peak
                    # eased: the bot stayed quiet while this said it would post.
                    "duplicate": bool(alert and row["kind"] in repo.WATCHED_KINDS
                                      and weather_alerts.is_repeat(alert, row.get("last_fingerprint")))})
