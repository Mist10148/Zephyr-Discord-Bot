"""Public weather and geocoding JSON endpoints."""

from datetime import datetime, timedelta, timezone

from flask import jsonify, request

from website.api import api, error
from website.api.cache import TTLCache
from website.api.guard import public_rate_limit
from zephyr.utils.weather_utils import (
    WeatherProviderError, class_suspension_payload, european_aqi_band,
    geocode_search, get_openmeteo_air_quality, get_openmeteo_bundle,
    slice_hourly_from, us_aqi_band, wmo_description, wmo_group, wmo_icon,
)

cache = TTLCache()


def _number(name: str, default: float | None = None) -> float | None:
    value = request.args.get(name)
    if value is None: return default
    try: return float(value)
    except ValueError: return None


def _time_payload(time_local: str | None, offset: int) -> dict:
    if not time_local: return {"time_local": None, "time_epoch": None}
    local = datetime.fromisoformat(time_local).replace(tzinfo=timezone(timedelta(seconds=offset)))
    return {"time_local": time_local, "time_epoch": int(local.timestamp())}


@api.get("/weather")
@public_rate_limit("weather", limit=60, window=60)
def weather():
    lat, lon = _number("lat"), _number("lon")
    units = request.args.get("units", "metric")
    hours = int(request.args.get("hours", 48)) if request.args.get("hours", "48").isdigit() else 48
    days = int(request.args.get("days", 7)) if request.args.get("days", "7").isdigit() else 7
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return error("invalid_coordinates", "lat and lon must be valid coordinates", 400)
    if units not in {"metric", "imperial"}: return error("invalid_units", "units must be metric or imperial", 400)
    key = f"weather:{lat:.2f}:{lon:.2f}:{units}:{hours}:{days}"
    try:
        bundle = cache.get_or_load(key, 300, lambda: get_openmeteo_bundle(lat, lon, units=units, hours=hours, days=days))
        try:
            aqi = cache.get_or_load(f"aqi:{lat:.2f}:{lon:.2f}", 900, lambda: get_openmeteo_air_quality(lat, lon))
            aqi_current = aqi.get("current") or {}
            air_quality = {"european_aqi": aqi_current.get("european_aqi"), "us_aqi": aqi_current.get("us_aqi"), "european_band": european_aqi_band(aqi_current.get("european_aqi")), "us_band": us_aqi_band(aqi_current.get("us_aqi")), "pm10": aqi_current.get("pm10"), "pm2_5": aqi_current.get("pm2_5"), "ozone": aqi_current.get("ozone"), "nitrogen_dioxide": aqi_current.get("nitrogen_dioxide")}
        except WeatherProviderError:
            air_quality = None
        current = bundle.get("current") or {}
        offset = bundle.get("utc_offset_seconds") or 0
        current_payload = {**_time_payload(current.get("time"), offset), "temperature": current.get("temperature_2m"), "feels_like": current.get("apparent_temperature"), "humidity": current.get("relative_humidity_2m"), "wind_speed": current.get("wind_speed_10m"), "precipitation": current.get("precipitation"), "weather_code": current.get("weather_code"), "description": wmo_description(current.get("weather_code")), "icon": wmo_icon(current.get("weather_code")), "group": wmo_group(current.get("weather_code"))}
        hourly = [{**row, **_time_payload(row.pop("time", None), offset)} for row in slice_hourly_from(bundle, max(1, min(hours, 168)))]
        daily_data, daily = bundle.get("daily") or {}, []
        for index, time_local in enumerate(daily_data.get("time") or []):
            code = daily_data["weather_code"][index]
            daily.append({"time_local": time_local, "time_epoch": _time_payload(f"{time_local}T00:00", offset)["time_epoch"], "weather_code": code, "description": wmo_description(code), "icon": wmo_icon(code), "temp_max": daily_data["temperature_2m_max"][index], "temp_min": daily_data["temperature_2m_min"][index], "feels_like_max": daily_data["apparent_temperature_max"][index], "feels_like_min": daily_data["apparent_temperature_min"][index], "precipitation_probability": daily_data["precipitation_probability_max"][index], "wind_speed_max": daily_data["wind_speed_10m_max"][index]})
        return jsonify({"latitude": lat, "longitude": lon, "timezone": bundle.get("timezone"), "utc_offset_seconds": offset, "units": units, "current": current_payload, "hourly": hourly, "daily": daily, "air_quality": air_quality, "class_suspension": class_suspension_payload(current.get("apparent_temperature"), units=units)})
    except WeatherProviderError as exc:
        return error("weather_unavailable", str(exc), 502)


@api.get("/geocode")
@public_rate_limit("geocode", limit=30, window=60)
def geocode():
    query = (request.args.get("q") or "").strip()
    if len(query) < 2: return error("invalid_query", "q must contain at least two characters", 400)
    count = min(max(int(request.args.get("count", 8)), 1), 10) if request.args.get("count", "8").isdigit() else 8
    try:
        results = cache.get_or_load(f"geocode:{query.lower()}:{count}", 86400, lambda: geocode_search(query, count))
        return jsonify({"results": [{"id": f"{item.get('id', '')}", "name": item.get("name"), "country": item.get("country"), "admin1": item.get("admin1"), "latitude": item.get("latitude"), "longitude": item.get("longitude"), "timezone": item.get("timezone"), "population": item.get("population"), "kind": item.get("feature_code")} for item in results]})
    except WeatherProviderError as exc:
        return error("geocode_unavailable", str(exc), 502)
