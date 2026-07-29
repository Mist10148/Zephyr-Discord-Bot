"""Shared Open-Meteo helpers used by both Discord and the public API."""

from datetime import datetime, timezone, timedelta
import threading
from typing import Any

import requests


FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
UNIT_PARAMS = {
    "metric": {"temperature_unit": "celsius", "wind_speed_unit": "kmh"},
    "imperial": {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph"},
}
UNIT_LABELS = {"metric": {"temperature": "°C", "wind": "km/h"}, "imperial": {"temperature": "°F", "wind": "mph"}}
_local = threading.local()


class WeatherProviderError(RuntimeError):
    """Base error for weather-provider failures."""


class WeatherTimeoutError(WeatherProviderError):
    """The upstream provider did not answer in time."""


class WeatherUpstreamError(WeatherProviderError):
    """The upstream provider returned an invalid or unsuccessful response."""


def _session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def _get_json(url: str, *, params: dict[str, Any], timeout: int = 15) -> dict:
    try:
        response = _session().get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.Timeout as exc:
        raise WeatherTimeoutError("Weather provider timed out") from exc
    except (requests.RequestException, ValueError) as exc:
        raise WeatherUpstreamError("Weather provider is unavailable") from exc


def get_tcws_description(wind_speed):
    if wind_speed >= 220: return "TCWS Level 5: Extremely Dangerous"
    if wind_speed >= 185: return "TCWS Level 4: Very Destructive"
    if wind_speed >= 118: return "TCWS Level 3: Destructive Typhoon"
    if wind_speed >= 62: return "TCWS Level 2: Threatening Typhoon"
    if wind_speed >= 30: return "TCWS Level 1: Tropical Cyclone Winds"
    return "No Tropical Cyclone Wind Signal"


def _format_datetime_pht(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%B %d, %Y at %I:%M %p PHT")


def _format_date_label(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %d, %Y")


_WMO_WEATHER_CODES = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle", 56: "Light freezing drizzle", 57: "Dense freezing drizzle", 61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain", 66: "Light freezing rain", 67: "Heavy freezing rain", 71: "Slight snow fall", 73: "Moderate snow fall", 75: "Heavy snow fall", 77: "Snow grains", 80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers", 85: "Slight snow showers", 86: "Heavy snow showers", 95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail"}
_WMO_ICONS = {code: ("sun.max" if code == 0 else "cloud.sun" if code in {1, 2} else "cloud" if code == 3 else "cloud.fog" if code in {45, 48} else "cloud.drizzle" if code in {51, 53, 55, 56, 57} else "cloud.rain" if code in {61, 63, 65, 66, 67, 80, 81, 82} else "cloud.snow" if code in {71, 73, 75, 77, 85, 86} else "cloud.bolt") for code in _WMO_WEATHER_CODES}


def wmo_description(code: int | None) -> str: return _WMO_WEATHER_CODES.get(code, "Unknown")
def wmo_icon(code: int | None) -> str: return _WMO_ICONS.get(code, "cloud")
def wmo_emoji(code: int | None) -> str: return "☀️" if code == 0 else "⛈️" if code in {95, 96, 99} else "🌧️" if code and code >= 51 else "☁️"
def wmo_group(code: int | None) -> str:
    if code == 0: return "clear"
    if code in {1, 2}: return "partly-cloudy"
    if code == 3: return "cloudy"
    if code in {45, 48}: return "fog"
    if code in {51, 53, 55, 56, 57}: return "drizzle"
    if code in {61, 63, 65, 66, 67, 80, 81, 82}: return "rain"
    if code in {71, 73, 75, 77, 85, 86}: return "snow"
    if code in {95, 96, 99}: return "storm"
    return "unknown"


def geocode_search(query: str, count: int = 8) -> list[dict]:
    data = _get_json(GEOCODE_URL, params={"name": query, "count": count, "language": "en", "format": "json"})
    results = data.get("results") or []
    indexed = enumerate(results)
    return [item for _, item in sorted(indexed, key=lambda pair: (pair[1].get("feature_code") not in {"PPLC", "PPLA", "PPLA2", "PPL", "PPLX"}, -(pair[1].get("population") or 0), pair[0]))]


def geocode_city(city: str) -> tuple[float, float] | None:
    try:
        results = geocode_search(city, 1)
        return (results[0]["latitude"], results[0]["longitude"]) if results else None
    except WeatherProviderError:
        return None


def get_openmeteo_bundle(lat: float, lon: float, *, units: str = "metric", hours: int = 48, days: int = 7) -> dict:
    if units not in UNIT_PARAMS: raise ValueError("units must be metric or imperial")
    return _get_json(FORECAST_URL, params={"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_hours": min(hours + 24, 168), "forecast_days": min(days, 16), "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation", "hourly": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,precipitation_probability,wind_speed_10m", "daily": "weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,precipitation_probability_max,wind_speed_10m_max", **UNIT_PARAMS[units]})


def slice_hourly_from(bundle: dict, hours: int) -> list[dict]:
    hourly = bundle.get("hourly") or {}
    current_time = (bundle.get("current") or {}).get("time", "")[:13]
    rows = []
    for index, time_local in enumerate(hourly.get("time") or []):
        if time_local[:13] < current_time: continue
        rows.append({key: (values[index] if index < len(values) else None) for key, values in hourly.items()})
        if len(rows) >= hours: break
    return rows


def get_openmeteo_air_quality(lat: float, lon: float) -> dict:
    return _get_json(AIR_QUALITY_URL, params={"latitude": lat, "longitude": lon, "timezone": "auto", "current": "european_aqi,us_aqi,pm10,pm2_5,ozone,nitrogen_dioxide"})


def european_aqi_band(value: float | None) -> str | None:
    if value is None: return None
    return next((name for limit, name in ((20, "good"), (40, "fair"), (60, "moderate"), (80, "poor")) if value <= limit), "very_poor")


def us_aqi_band(value: float | None) -> str | None:
    if value is None: return None
    return next((name for limit, name in ((50, "good"), (100, "moderate"), (150, "unhealthy_sensitive"), (200, "unhealthy"), (300, "very_unhealthy")) if value <= limit), "hazardous")


def class_suspension_payload(apparent_temp: float | None, *, units: str = "metric") -> dict:
    if apparent_temp is None: return {"level": "unknown", "reason": None}
    celsius = (apparent_temp - 32) * 5 / 9 if units == "imperial" else apparent_temp
    if celsius >= 50: return {"level": "certain", "reason": "Excessive Heat"}
    if celsius >= 41: return {"level": "likely", "reason": "Extreme Heat"}
    if celsius >= 38: return {"level": "possible", "reason": "Dangerous Heat"}
    if celsius <= 15: return {"level": "possible", "reason": "Cold Weather"}
    return {"level": "none", "reason": None}


# Legacy wrappers retain the exact dict keys consumed by the Discord cog.
def get_openmeteo_daily_forecast(lat: float, lon: float, days: int = 3) -> list[dict]:
    data = get_openmeteo_bundle(lat, lon, days=days).get("daily", {})
    return [{"date": data["time"][i], "weather_code": data["weather_code"][i], "temp_max": data["temperature_2m_max"][i], "temp_min": data["temperature_2m_min"][i], "feels_like_max": data["apparent_temperature_max"][i], "feels_like_min": data["apparent_temperature_min"][i], "precipitation_probability": data["precipitation_probability_max"][i], "wind_speed_max": data["wind_speed_10m_max"][i]} for i in range(len(data.get("time", [])))]


def get_openmeteo_current(lat: float, lon: float) -> dict:
    cur = get_openmeteo_bundle(lat, lon, hours=1, days=1)["current"]
    return {"temp": cur["temperature_2m"], "humidity": cur["relative_humidity_2m"], "apparent_temp": cur["apparent_temperature"], "weather_code": cur["weather_code"], "wind_speed": cur["wind_speed_10m"]}
