"""Deciding what a weather subscription should say.

Pure functions over an Open-Meteo bundle: no Discord, no database, no network.
That is what lets the dashboard's preview endpoint and the bot's scheduler render
the *same* alert -- a preview computed by different code would be a preview of
something else.

Every alert carries a ``fingerprint``.  The severe watcher runs every fifteen
minutes and the same storm is still there on the next tick, so without one the
channel would receive four identical warnings an hour until the weather changed.
The fingerprint is deliberately coarse -- values are bucketed before hashing --
so a gust wobbling by 2 km/h is the same alert, while a genuine escalation is a
new one.
"""

import hashlib
import json
import math

from zephyr.utils.weather_utils import (
    UNIT_LABELS,
    class_suspension_payload,
    slice_hourly_from,
    wmo_description,
    wmo_emoji,
)

# Metric defaults.  An imperial subscription stores its own numbers, converted
# with ``convert_thresholds`` at subscription time, so a threshold is always in
# the same unit as the bundle it is compared against.
DEFAULT_THRESHOLDS = {
    "wind_speed": 60.0,
    "precipitation_probability": 80.0,
    "apparent_temperature": 38.0,
    "storm": True,
}

# Allowed range per numeric threshold, in metric units.  Imperial values are
# checked after converting back.
THRESHOLD_RANGES = {
    "wind_speed": (0.0, 400.0),
    "precipitation_probability": (0.0, 100.0),
    "apparent_temperature": (-60.0, 70.0),
}

KMH_PER_MPH = 1.609344


def _to_unit(key: str, value: float, units: str) -> float:
    if units != "imperial":
        return value
    if key == "wind_speed":
        return round(value / KMH_PER_MPH, 1)
    if key == "apparent_temperature":
        return round(value * 9 / 5 + 32, 1)
    return value


def _to_metric(key: str, value: float, units: str) -> float:
    if units != "imperial":
        return value
    if key == "wind_speed":
        return value * KMH_PER_MPH
    if key == "apparent_temperature":
        return (value - 32) * 5 / 9
    return value


def convert_thresholds(thresholds: dict | None, from_units: str, to_units: str) -> dict | None:
    """Re-express thresholds in another unit system; None and booleans pass through."""
    if thresholds is None or from_units == to_units:
        return thresholds
    converted = {}
    for key, value in thresholds.items():
        if isinstance(value, bool) or value is None or key not in THRESHOLD_RANGES:
            converted[key] = value
        else:
            converted[key] = _to_unit(key, _to_metric(key, float(value), from_units), to_units)
    return converted


def thresholds_for(units: str) -> dict:
    """The default thresholds in ``units``.

    Imperial watches used to be compared against the metric numbers: 38 became
    38 °F, so a "feels like 95°F" warning posted every day, and 60 became
    60 mph, so real gales were missed.
    """
    return convert_thresholds(dict(DEFAULT_THRESHOLDS), "metric", units)


def threshold_in_range(key: str, value: float, units: str) -> bool:
    low, high = THRESHOLD_RANGES[key]
    return math.isfinite(value) and low <= _to_metric(key, value, units) <= high

STORM_CODES = {95, 96, 99}
# How far ahead the severe watcher looks.  Long enough to be a warning, short
# enough that it is about today rather than a forecast.
SEVERE_HOURS = 12


def _round_to(value: float | None, step: float) -> float | None:
    if value is None:
        return None
    return round(value / step) * step


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _daily(bundle: dict, index: int = 0) -> dict:
    daily = bundle.get("daily") or {}
    times = daily.get("time") or []
    if index >= len(times):
        return {}
    return {key: (values[index] if index < len(values) else None) for key, values in daily.items()}


def build_daily_digest(bundle: dict, *, location: str, units: str = "metric") -> dict:
    """Today's forecast, as a renderable dict.

    Returns the same shape the API's preview endpoint serves, so what a person
    sees before subscribing is what the channel receives afterwards.
    """
    labels = UNIT_LABELS.get(units, UNIT_LABELS["metric"])
    today = _daily(bundle)
    current = bundle.get("current") or {}
    code = today.get("weather_code")

    fields = [
        ("High / Low", _range(today.get("temperature_2m_max"), today.get("temperature_2m_min"), labels["temperature"])),
        ("Feels like", _range(today.get("apparent_temperature_max"), today.get("apparent_temperature_min"), labels["temperature"])),
        ("Chance of rain", _value(today.get("precipitation_probability_max"), "%")),
        ("Max wind", _value(today.get("wind_speed_10m_max"), f" {labels['wind']}")),
        ("Right now", _value(current.get("temperature_2m"), labels["temperature"])),
    ]
    suspension = class_suspension_payload(today.get("apparent_temperature_max"), units=units)
    return {
        "kind": "daily",
        "title": f"{wmo_emoji(code)} Today in {location}",
        "summary": wmo_description(code),
        "date": today.get("time"),
        "fields": [{"name": name, "value": value} for name, value in fields],
        "class_suspension": suspension,
        "fingerprint": _fingerprint({"kind": "daily", "date": today.get("time"), "location": location}),
    }


def evaluate_severe(bundle: dict, thresholds: dict | None, *, location: str, units: str = "metric") -> dict | None:
    """What is about to be bad in the next few hours, or None.

    None means "nothing crossed a threshold" -- a quiet result the caller must
    not post, and must not record as a run either, or the next genuine warning
    would be suppressed as a duplicate.
    """
    if units == "imperial" and thresholds == DEFAULT_THRESHOLDS:
        # Rows saved before imperial defaults were converted hold the metric
        # numbers verbatim; read them as the defaults they were meant to be.
        thresholds = None
    limits = {**thresholds_for(units), **(thresholds or {})}
    labels = UNIT_LABELS.get(units, UNIT_LABELS["metric"])
    hours = slice_hourly_from(bundle, SEVERE_HOURS)
    if not hours:
        return None

    def peak(key):
        values = [row.get(key) for row in hours if row.get(key) is not None]
        return max(values) if values else None

    wind = peak("wind_speed_10m")
    rain = peak("precipitation_probability")
    feels = peak("apparent_temperature")
    storm_codes = sorted({row.get("weather_code") for row in hours if row.get("weather_code") in STORM_CODES})

    reasons, buckets = [], {}
    if limits.get("wind_speed") is not None and wind is not None and wind >= limits["wind_speed"]:
        reasons.append(f"Wind up to {wind:.0f} {labels['wind']}")
        buckets["wind"] = _round_to(wind, 10)
    if limits.get("precipitation_probability") is not None and rain is not None and rain >= limits["precipitation_probability"]:
        reasons.append(f"{rain:.0f}% chance of rain")
        buckets["rain"] = _round_to(rain, 10)
    if limits.get("apparent_temperature") is not None and feels is not None and feels >= limits["apparent_temperature"]:
        reasons.append(f"Feels like {feels:.0f}{labels['temperature']}")
        buckets["feels"] = _round_to(feels, 1)
    if limits.get("storm") and storm_codes:
        reasons.append(wmo_description(storm_codes[0]))
        buckets["storm"] = storm_codes

    if not reasons:
        return None
    return {
        "kind": "severe",
        "title": f"⚠️ Severe weather watch — {location}",
        "summary": f"In the next {SEVERE_HOURS} hours: " + "; ".join(reasons) + ".",
        "reasons": reasons,
        "fields": [{"name": "Watch window", "value": f"Next {SEVERE_HOURS} hours"}],
        "fingerprint": _fingerprint({"kind": "severe", "location": location, **buckets}),
    }


def evaluate_class_suspension(bundle: dict, *, location: str, units: str = "metric") -> dict | None:
    """An advisory when today's heat index reaches a suspension-worthy level.

    Advisory only -- it is a heat-index reading, not an announcement from any
    authority, and the message says so rather than leaving a reader to assume.
    """
    today = _daily(bundle)
    payload = class_suspension_payload(today.get("apparent_temperature_max"), units=units)
    if not is_heat_advisory(payload):
        return None
    labels = UNIT_LABELS.get(units, UNIT_LABELS["metric"])
    feels = today.get("apparent_temperature_max")
    return {
        "kind": "class_suspension",
        "title": f"🌡️ Class suspension watch — {location}",
        "summary": f"Suspension is **{payload['level']}** today ({payload['reason']}).",
        "level": payload["level"],
        "fields": [
            {"name": "Feels like (max)", "value": _value(feels, labels["temperature"])},
            {"name": "Advisory only", "value": "Based on the heat index. Always confirm with your school or local government."},
        ],
        # Keyed on the day and the level, so an escalation posts again but a
        # steady forecast does not repeat every quarter of an hour.
        "fingerprint": _fingerprint(
            {"kind": "class_suspension", "location": location, "date": today.get("time"), "level": payload["level"]}
        ),
    }


def is_heat_advisory(payload: dict | None) -> bool:
    """Whether a class-suspension payload is a heat-index advisory.

    The shared scale also rates a cool day (<= 15 °C) as "possible" for /class.
    The watch and the digest's Classes line are documented as heat-index
    advisories, and counting cold made every cool city post one daily.
    """
    payload = payload or {}
    return payload.get("level") in {"certain", "likely", "possible"} and payload.get("reason") != "Cold Weather"


def _value(value, suffix: str) -> str:
    return "—" if value is None else f"{value:g}{suffix}"


def _range(high, low, suffix: str) -> str:
    if high is None and low is None:
        return "—"
    return f"{_value(high, suffix)} / {_value(low, suffix)}"


def evaluate(kind: str, bundle: dict, *, location: str, units: str = "metric", thresholds: dict | None = None):
    """Dispatch by subscription kind.  Unknown kinds are quiet, not fatal."""
    if kind == "daily":
        return build_daily_digest(bundle, location=location, units=units)
    if kind == "severe":
        return evaluate_severe(bundle, thresholds, location=location, units=units)
    if kind == "class_suspension":
        return evaluate_class_suspension(bundle, location=location, units=units)
    return None
