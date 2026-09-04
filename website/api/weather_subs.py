"""Weather subscription CRUD, plus a preview.

The preview calls the same pure evaluators the bot's scheduler does
(``zephyr/utils/weather_alerts.py``), so what somebody sees before saving is what
the channel receives afterwards.  Rendering it separately here would have been
easier and wrong.
"""

from flask import current_app, g, jsonify, request

from website.api import api, error
from website.api.cache import TTLCache
from website.api.guard import guild_scoped
from zephyr.db import audit
from zephyr.db import weather_subs as repo
from zephyr.utils import weather_alerts
from zephyr.utils.weather_utils import WeatherProviderError, geocode_search, get_openmeteo_bundle
from datetime import datetime, timezone

# Shared with the public weather endpoint's reasoning: a forecast bundle is worth
# reusing for a few minutes, and a preview is often reloaded while somebody
# fiddles with thresholds.
cache = TTLCache()

CREATE_REQUIRED = {"kind", "location", "channel_id"}
EDITABLE = {"channel_id", "kind", "location", "schedule_local_time", "tz", "thresholds", "enabled", "units", "muted_until"}


def _database_url():
    return current_app.config["DATABASE_URL"]


def _public(row: dict) -> dict:
    return {
        **row,
        "id": row["id"],
        "last_run_at": row["last_run_at"].isoformat() if row.get("last_run_at") else None,
        # Snoozing is not disabling: the row keeps its settings and its
        # schedule and only goes quiet, so the UI has to be able to tell the
        # two states apart.
        "muted_until": row["muted_until"].isoformat() if row.get("muted_until") else None,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "kind_label": {"daily": "Daily digest", "severe": "Severe weather watch",
                       "class_suspension": "Class suspension watch"}.get(row["kind"], row["kind"]),
    }


def _clean_muted_until(value):
    """An ISO instant, or null to unmute.

    Accepts a Z suffix, because that is what JavaScript's toISOString emits and
    fromisoformat did not accept it before 3.11 -- normalising here means the
    dashboard does not have to know.
    """
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("muted_until must be an ISO timestamp, or null to unmute.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _clean_thresholds(value):
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
            cleaned[key] = bool(raw)
            continue
        if raw is None:
            # None disables one threshold without disabling the subscription --
            # "warn me about wind but never about rain" has to be expressible.
            cleaned[key] = None
            continue
        try:
            cleaned[key] = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number.") from None
    return cleaned


def _clean_common(body: dict, *, partial: bool) -> dict:
    values = {}
    if "kind" in body:
        if body["kind"] not in repo.KINDS:
            raise ValueError(f"kind must be one of {', '.join(repo.KINDS)}.")
        values["kind"] = body["kind"]
    if "channel_id" in body:
        if not str(body["channel_id"]).isdigit():
            raise ValueError("channel_id must be a Discord id.")
        values["channel_id"] = str(body["channel_id"])
    if "units" in body:
        if body["units"] not in {"metric", "imperial"}:
            raise ValueError("units must be metric or imperial.")
        values["units"] = body["units"]
    if "tz" in body:
        zone_name, accepted = repo.normalise_zone(body["tz"])
        if not accepted:
            raise ValueError("tz must be an IANA timezone name, like Asia/Manila.")
        values["tz"] = zone_name
    if "schedule_local_time" in body:
        raw = body["schedule_local_time"]
        values["schedule_local_time"] = repo.parse_local_time(raw).strftime("%H:%M") if raw else None
    if "thresholds" in body:
        values["thresholds"] = _clean_thresholds(body["thresholds"])
    if "enabled" in body:
        values["enabled"] = bool(body["enabled"])
    if "muted_until" in body:
        # Raises rather than returning a response: this function is the shared
        # field parser for both create and patch, and its callers turn a
        # ValueError into a 400. Returning here made the *caller* treat an error
        # tuple as the values dict.
        values["muted_until"] = _clean_muted_until(body["muted_until"])

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

    location = str(body.get("location") or "").strip()
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
        values.setdefault("thresholds", dict(weather_alerts.DEFAULT_THRESHOLDS))

    try:
        created = repo.create(values, database_url=_database_url())
    except repo.SubscriptionError as exc:
        return error("invalid_value", str(exc), 400)

    audit.record("weather_sub.create", actor_id=g.zephyr_session.user_id, guild_id=guild_id,
                 payload={"id": created["id"], "kind": created["kind"], "location": created["location"]},
                 source="web", database_url=_database_url())
    return jsonify(_public(created)), 201


def _owned(guild_id: str, sub_id: str):
    if not str(sub_id).isdigit():
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
        values = _clean_common(body, partial=True)
    except (ValueError, repo.SubscriptionError) as exc:
        return error("invalid_value", str(exc), 400)

    if "location" in body:
        location = str(body["location"]).strip()
        try:
            places = geocode_search(location, 1)
        except WeatherProviderError as exc:
            return error("geocode_unavailable", str(exc), 502)
        if not places:
            return error("invalid_value", f"Could not find {location}.", 400)
        values.update({"location": places[0].get("name") or location,
                       "lat": places[0]["latitude"], "lon": places[0]["longitude"]})

    # Checked against the merged result, not the patch: switching an existing
    # severe watch to a daily digest must not leave it without a time.
    merged_kind = values.get("kind", row["kind"])
    merged_time = values.get("schedule_local_time", row["schedule_local_time"])
    if merged_kind == "daily" and not merged_time:
        return error("invalid_value", "A daily digest needs a time, like 08:00.", 400)

    if not values:
        return error("invalid_body", "Nothing to change.", 400)
    updated = repo.update_sub(int(sub_id), values, database_url=_database_url())
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

    alert = weather_alerts.evaluate(row["kind"], bundle, location=row["location"], units=units,
                                    thresholds=row.get("thresholds"))
    return jsonify({"id": row["id"], "kind": row["kind"], "alert": alert,
                    "would_post": alert is not None,
                    "duplicate": bool(alert and alert["fingerprint"] == row.get("last_fingerprint"))})
