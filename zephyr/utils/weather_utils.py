"""Shared weather helpers."""

from datetime import datetime, timezone, timedelta

import requests


def get_tcws_description(wind_speed):
    if wind_speed >= 220:
        return "TCWS Level 5: Extremely Dangerous"
    elif wind_speed >= 185:
        return "TCWS Level 4: Very Destructive"
    elif wind_speed >= 118:
        return "TCWS Level 3: Destructive Typhoon"
    elif wind_speed >= 62:
        return "TCWS Level 2: Threatening Typhoon"
    elif wind_speed >= 30:
        return "TCWS Level 1: Tropical Cyclone Winds"
    return "No Tropical Cyclone Wind Signal"


def _format_datetime_pht(timestamp: int) -> str:
    pht_tz = timezone(timedelta(hours=8))
    utc_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    pht_dt = utc_dt.astimezone(pht_tz)
    return pht_dt.strftime('%B %d, %Y at %I:%M %p PHT')


def _format_date_label(date_str: str) -> str:
    """Turn an Open-Meteo YYYY-MM-DD string into a friendly label."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%A, %B %d, %Y")


# ---------------------------------------------------------------------------
# Open-Meteo helpers
# ---------------------------------------------------------------------------
_WMO_WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def wmo_description(code: int | None) -> str:
    """Return a human-readable description for a WMO weather code."""
    if code is None:
        return "Unknown"
    return _WMO_WEATHER_CODES.get(code, "Unknown")


def geocode_city(city: str) -> tuple[float, float] | None:
    """Resolve a city name to (latitude, longitude) using Open-Meteo geocoding."""
    try:
        resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en", "format": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if not results:
            return None
        return results[0]["latitude"], results[0]["longitude"]
    except Exception:
        return None


def get_openmeteo_daily_forecast(lat: float, lon: float, days: int = 3) -> list[dict]:
    """Fetch daily forecast summaries from Open-Meteo.

    Returns a list of dicts with date, weather_code, temp_max, temp_min,
    feels_like_max, feels_like_min, precipitation_probability, wind_speed_max.
    """
    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": ",".join([
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "apparent_temperature_max",
                "apparent_temperature_min",
                "precipitation_probability_max",
                "wind_speed_10m_max",
            ]),
            "timezone": "auto",
            "forecast_days": days,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()["daily"]

    days_out = []
    for i in range(len(data["time"])):
        days_out.append({
            "date": data["time"][i],
            "weather_code": data["weather_code"][i],
            "temp_max": data["temperature_2m_max"][i],
            "temp_min": data["temperature_2m_min"][i],
            "feels_like_max": data["apparent_temperature_max"][i],
            "feels_like_min": data["apparent_temperature_min"][i],
            "precipitation_probability": data["precipitation_probability_max"][i],
            "wind_speed_max": data["wind_speed_10m_max"][i],
        })
    return days_out


def get_openmeteo_current(lat: float, lon: float) -> dict:
    """Fetch current conditions from Open-Meteo.

    Returns temp, humidity, apparent_temp (feels like), weather_code, wind_speed.
    """
    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": ",".join([
                "temperature_2m",
                "relative_humidity_2m",
                "apparent_temperature",
                "weather_code",
                "wind_speed_10m",
            ]),
            "timezone": "auto",
        },
        timeout=10,
    )
    resp.raise_for_status()
    cur = resp.json()["current"]
    return {
        "temp": cur["temperature_2m"],
        "humidity": cur["relative_humidity_2m"],
        "apparent_temp": cur["apparent_temperature"],
        "weather_code": cur["weather_code"],
        "wind_speed": cur["wind_speed_10m"],
    }
