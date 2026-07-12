"""Zephyr Weather Dashboard — Flask backend.

Serves a React + Vite frontend from website/static/ and exposes a JSON API
for current weather, forecasts, AQI, alerts, and city autocomplete.
"""

import os
import re
from datetime import datetime, timezone
from functools import lru_cache

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder

from zephyr.config import (
    API_KEY,
    CURRENT_URL,
    FORECAST_URL,
    ALERTS_URL,
    PHILIPPINE_COORDS,
    ILOILO_COORDS,
)

app = Flask(__name__, static_folder="static")
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OWM_API_KEY = API_KEY
DEFAULT_CITY = "Iloilo City, Philippines"
DEFAULT_LAT = ILOILO_COORDS["lat"]
DEFAULT_LON = ILOILO_COORDS["lon"]

# ---------------------------------------------------------------------------
# City autocomplete data (curated, fast, zero-rate-limit)
# ---------------------------------------------------------------------------
CITIES = [
    ("Iloilo City", "PH", 10.7202, 122.5621),
    ("Manila", "PH", 14.5995, 120.9842),
    ("Cebu City", "PH", 10.3157, 123.8854),
    ("Davao City", "PH", 7.1907, 125.4553),
    ("Baguio", "PH", 16.4023, 120.5960),
    ("Tokyo", "JP", 35.6762, 139.6503),
    ("Osaka", "JP", 34.6937, 135.5023),
    ("Kyoto", "JP", 35.0116, 135.7681),
    ("Seoul", "KR", 37.5665, 126.9780),
    ("Busan", "KR", 35.1796, 129.0756),
    ("Beijing", "CN", 39.9042, 116.4074),
    ("Shanghai", "CN", 31.2304, 121.4737),
    ("Hong Kong", "HK", 22.3193, 114.1694),
    ("Taipei", "TW", 25.0330, 121.5654),
    ("Singapore", "SG", 1.3521, 103.8198),
    ("Bangkok", "TH", 13.7563, 100.5018),
    ("Jakarta", "ID", -6.2088, 106.8456),
    ("Kuala Lumpur", "MY", 3.1390, 101.6869),
    ("Ho Chi Minh City", "VN", 10.8231, 106.6297),
    ("Hanoi", "VN", 21.0278, 105.8342),
    ("Mumbai", "IN", 19.0760, 72.8777),
    ("New Delhi", "IN", 28.6139, 77.2090),
    ("Bangalore", "IN", 12.9716, 77.5946),
    ("Dubai", "AE", 25.2048, 55.2708),
    ("Sydney", "AU", -33.8688, 151.2093),
    ("Melbourne", "AU", -37.8136, 144.9631),
    ("London", "GB", 51.5074, -0.1278),
    ("Manchester", "GB", 53.4808, -2.2426),
    ("Paris", "FR", 48.8566, 2.3522),
    ("Lyon", "FR", 45.7640, 4.8357),
    ("Berlin", "DE", 52.5200, 13.4050),
    ("Munich", "DE", 48.1351, 11.5820),
    ("Hamburg", "DE", 53.5511, 9.9937),
    ("Madrid", "ES", 40.4168, -3.7038),
    ("Barcelona", "ES", 41.3851, 2.1734),
    ("Rome", "IT", 41.9028, 12.4964),
    ("Milan", "IT", 45.4642, 9.1900),
    ("Amsterdam", "NL", 52.3676, 4.9041),
    ("Brussels", "BE", 50.8503, 4.3517),
    ("Vienna", "AT", 48.2082, 16.3738),
    ("Zurich", "CH", 47.3769, 8.5417),
    ("Stockholm", "SE", 59.3293, 18.0686),
    ("Copenhagen", "DK", 55.6761, 12.5683),
    ("Oslo", "NO", 59.9139, 10.7522),
    ("Helsinki", "FI", 60.1699, 24.9384),
    ("Warsaw", "PL", 52.2297, 21.0122),
    ("Prague", "CZ", 50.0755, 14.4378),
    ("Budapest", "HU", 47.4979, 19.0402),
    ("Lisbon", "PT", 38.7223, -9.1393),
    ("Athens", "GR", 37.9838, 23.7275),
    ("Istanbul", "TR", 41.0082, 28.9784),
    ("Moscow", "RU", 55.7558, 37.6173),
    ("Saint Petersburg", "RU", 59.9311, 30.3609),
    ("New York", "US", 40.7128, -74.0060),
    ("Los Angeles", "US", 34.0522, -118.2437),
    ("Chicago", "US", 41.8781, -87.6298),
    ("Houston", "US", 29.7604, -95.3698),
    ("Phoenix", "US", 33.4484, -112.0740),
    ("Philadelphia", "US", 39.9526, -75.1652),
    ("San Antonio", "US", 29.4241, -98.4936),
    ("San Diego", "US", 32.7157, -117.1611),
    ("Dallas", "US", 32.7767, -96.7970),
    ("San Jose", "US", 37.3382, -121.8863),
    ("San Francisco", "US", 37.7749, -122.4194),
    ("Seattle", "US", 47.6062, -122.3321),
    ("Miami", "US", 25.7617, -80.1918),
    ("Boston", "US", 42.3601, -71.0589),
    ("Las Vegas", "US", 36.1699, -115.1398),
    ("Toronto", "CA", 43.6532, -79.3832),
    ("Vancouver", "CA", 49.2827, -123.1207),
    ("Montreal", "CA", 45.5017, -73.5673),
    ("Mexico City", "MX", 19.4326, -99.1332),
    ("Guadalajara", "MX", 20.6597, -103.3496),
    ("São Paulo", "BR", -23.5505, -46.6333),
    ("Rio de Janeiro", "BR", -22.9068, -43.1729),
    ("Buenos Aires", "AR", -34.6037, -58.3816),
    ("Lima", "PE", -12.0464, -77.0428),
    ("Bogotá", "CO", 4.7110, -74.0721),
    ("Santiago", "CL", -33.4489, -70.6693),
    ("Caracas", "VE", 10.4806, -66.9036),
    ("Cairo", "EG", 30.0444, 31.2357),
    ("Lagos", "NG", 6.5244, 3.3792),
    ("Nairobi", "KE", -1.2921, 36.8219),
    ("Johannesburg", "ZA", -26.2041, 28.0473),
    ("Cape Town", "ZA", -33.9249, 18.4241),
    ("Tel Aviv", "IL", 32.0853, 34.7818),
    ("Jerusalem", "IL", 31.7683, 35.2137),
    ("Riyadh", "SA", 24.7136, 46.6753),
    ("Doha", "QA", 25.2854, 51.5310),
    ("Kuwait City", "KW", 29.3759, 47.9774),
    ("Tehran", "IR", 35.6892, 51.3890),
    ("Karachi", "PK", 24.8607, 67.0011),
    ("Lahore", "PK", 31.5204, 74.3587),
    ("Dhaka", "BD", 23.8103, 90.4125),
    ("Colombo", "LK", 6.9271, 79.8612),
    ("Kathmandu", "NP", 27.7172, 85.3240),
    ("Yangon", "MM", 16.8661, 96.1951),
    ("Phnom Penh", "KH", 11.5564, 104.9282),
    ("Auckland", "NZ", -36.8485, 174.7633),
    ("Wellington", "NZ", -41.2865, 174.7762),
    ("Brisbane", "AU", -27.4698, 153.0251),
    ("Perth", "AU", -31.9505, 115.8605),
    ("Adelaide", "AU", -34.9285, 138.6007),
]


def _city_to_dict(city_tuple):
    name, country, lat, lon = city_tuple
    return {"name": name, "country": country, "lat": lat, "lon": lon}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def c_to_f(celsius):
    if celsius is None:
        return None
    return round((float(celsius) * 9 / 5) + 32)


def normalize_condition(weather_id, icon_code=""):
    """Map OpenWeatherMap condition codes to a small set of UI themes."""
    if 200 <= weather_id <= 232:
        return "storm"
    if 300 <= weather_id <= 321:
        return "rain"
    if 500 <= weather_id <= 531:
        return "rain"
    if 600 <= weather_id <= 622:
        return "snow"
    if 701 <= weather_id <= 762:
        return "mist"
    if weather_id == 771 or weather_id == 781:
        return "storm"
    if weather_id == 800:
        return "clear"
    if 801 <= weather_id <= 802:
        return "cloudy"
    if 803 <= weather_id <= 804:
        return "cloudy"
    if icon_code.endswith("d"):
        return "clear"
    return "cloudy"


def aqi_label(aqi_value):
    labels = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}
    return labels.get(aqi_value, "Unknown")


def format_time(dt_obj):
    return dt_obj.strftime("%I %p").lstrip("0")


def format_weekday(dt_obj):
    return dt_obj.strftime("%A")


def format_date(dt_obj):
    return dt_obj.strftime("%b %d")


def fetch_openweather(url, params):
    """Make a GET request to OpenWeatherMap and return JSON or raise an HTTPError."""
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


@lru_cache(maxsize=128)
def find_timezone(lat, lon):
    try:
        tf = TimezoneFinder()
        tz_name = tf.timezone_at(lng=float(lon), lat=float(lat))
        return tz_name or "UTC"
    except Exception:
        return "UTC"


def geocode_city(city_name):
    """Return (lat, lon, city, country) or None if not found."""
    geolocator = Nominatim(user_agent="zephyr_weather_dashboard")
    location = geolocator.geocode(city_name, language="en", timeout=10)
    if not location:
        return None
    return {
        "lat": location.latitude,
        "lon": location.longitude,
        "city": location.raw.get("name", location.address.split(",")[0]),
        "country": location.raw.get("address", {}).get("country_code", "").upper(),
        "display_name": location.address,
    }


def build_hourly(forecast_list):
    """Build a 24-hour breakdown from 3-hour forecast entries."""
    hourly = []
    for entry in forecast_list[:8]:
        dt = datetime.utcfromtimestamp(entry.get("dt", 0))
        temp = entry.get("main", {}).get("temp")
        pop = entry.get("pop", 0)
        icon = entry.get("weather", [{}])[0].get("icon", "")
        hourly.append({
            "time": format_time(dt),
            "temp_c": round(temp) if temp is not None else None,
            "temp_f": c_to_f(temp),
            "pop": round(float(pop) * 100),
            "icon": icon,
        })
    return hourly


def build_daily(forecast_list):
    """Group 3-hour forecast entries into day/night summaries for each calendar day."""
    days = {}
    for entry in forecast_list:
        dt = datetime.utcfromtimestamp(entry.get("dt", 0))
        date_key = dt.strftime("%Y-%m-%d")
        if date_key not in days:
            days[date_key] = {
                "entries": [],
                "weekday": format_weekday(dt),
                "date": format_date(dt),
            }
        days[date_key]["entries"].append(entry)

    daily = []
    for date_key, day_data in sorted(days.items())[:4]:
        entries = day_data["entries"]
        if not entries:
            continue

        # Day = entry closest to 12:00 UTC; Night = entry closest to 00:00 UTC
        day_entry = min(entries, key=lambda e: abs(datetime.utcfromtimestamp(e["dt"]).hour - 12))
        night_entry = min(entries, key=lambda e: abs(datetime.utcfromtimestamp(e["dt"]).hour - 0))

        day_main = day_entry.get("main", {})
        night_main = night_entry.get("main", {})
        day_weather = day_entry.get("weather", [{}])[0]
        night_weather = night_entry.get("weather", [{}])[0]

        daily.append({
            "date": day_data["date"],
            "weekday": day_data["weekday"],
            "day_temp_c": round(day_main.get("temp")) if day_main.get("temp") is not None else None,
            "day_temp_f": c_to_f(day_main.get("temp")),
            "night_temp_c": round(night_main.get("temp")) if night_main.get("temp") is not None else None,
            "night_temp_f": c_to_f(night_main.get("temp")),
            "day_desc": day_weather.get("description", ""),
            "night_desc": night_weather.get("description", ""),
            "day_humidity": day_main.get("humidity"),
            "night_humidity": night_main.get("humidity"),
            "day_wind": day_entry.get("wind", {}).get("speed"),
            "night_wind": night_entry.get("wind", {}).get("speed"),
            "day_pressure": day_main.get("pressure"),
            "night_pressure": night_main.get("pressure"),
            "icon": day_weather.get("icon", ""),
        })
    return daily


def fetch_alerts(lat, lon):
    """Try One Call 3.0 for alerts. Returns [] if not configured or the plan lacks access."""
    if not ALERTS_URL or not OWM_API_KEY:
        return []
    try:
        data = fetch_openweather(
            ALERTS_URL,
            {"lat": lat, "lon": lon, "appid": OWM_API_KEY, "exclude": "current,minutely,hourly,daily"},
        )
        alerts = data.get("alerts", [])
        return [
            {
                "event": alert.get("event", "Weather Alert"),
                "description": alert.get("description", ""),
                "start": alert.get("start"),
                "end": alert.get("end"),
            }
            for alert in alerts
        ]
    except Exception:
        return []


def fetch_aqi(lat, lon):
    """Fetch Air Quality Index from OpenWeatherMap Air Pollution API."""
    try:
        data = fetch_openweather(
            "http://api.openweathermap.org/data/2.5/air_pollution",
            {"lat": lat, "lon": lon, "appid": OWM_API_KEY},
        )
        aqi = data.get("list", [{}])[0].get("main", {}).get("aqi")
        return {"value": aqi, "label": aqi_label(aqi)}
    except Exception:
        return {"value": None, "label": "Unknown"}


def build_weather_payload(lat, lon, city, country, timezone_str):
    """Coordinate OWM calls and return a unified dashboard payload."""
    current = fetch_openweather(
        CURRENT_URL,
        {"lat": lat, "lon": lon, "appid": OWM_API_KEY, "units": "metric"},
    )
    forecast = fetch_openweather(
        FORECAST_URL,
        {"lat": lat, "lon": lon, "appid": OWM_API_KEY, "units": "metric"},
    )

    current_main = current.get("main", {})
    current_wind = current.get("wind", {})
    current_weather = current.get("weather", [{}])[0]
    condition = normalize_condition(current_weather.get("id", 800), current_weather.get("icon", ""))

    temp = current_main.get("temp")
    feels_like = current_main.get("feels_like")

    hourly = build_hourly(forecast.get("list", []))
    daily = build_daily(forecast.get("list", []))
    aqi = fetch_aqi(lat, lon)
    alerts = fetch_alerts(lat, lon)

    return {
        "city": city,
        "country": country,
        "timezone": timezone_str,
        "lat": lat,
        "lon": lon,
        "current": {
            "temp_c": round(temp) if temp is not None else None,
            "temp_f": c_to_f(temp),
            "feels_like_c": round(feels_like) if feels_like is not None else None,
            "feels_like_f": c_to_f(feels_like),
            "description": current_weather.get("description", ""),
            "icon": current_weather.get("icon", ""),
            "condition": condition,
            "humidity": current_main.get("humidity"),
            "wind_speed": current_wind.get("speed"),
            "pressure": current_main.get("pressure"),
            "uvi": None,  # Free tier does not expose UVI easily
            "aqi": aqi.get("value"),
            "aqi_label": aqi.get("label"),
        },
        "hourly": hourly,
        "daily": daily,
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    """Serve the built React app."""
    return send_from_directory(app.static_folder, "index.html")


@app.route("/<path:path>")
def static_files(path):
    """Serve static assets generated by the React build (JS, CSS, images)."""
    return send_from_directory(app.static_folder, path)


@app.route("/health")
def health():
    """Health check used by load balancers and deployment platforms."""
    return jsonify({"status": "ok"}), 200


@app.route("/api/weather/default", methods=["GET"])
def weather_default():
    """Return weather data for the default city (Iloilo City)."""
    if not OWM_API_KEY:
        return jsonify({"error": "OpenWeatherMap API key is not configured."}), 500

    timezone_str = find_timezone(DEFAULT_LAT, DEFAULT_LON)
    try:
        payload = build_weather_payload(
            DEFAULT_LAT,
            DEFAULT_LON,
            "Iloilo City",
            "PH",
            timezone_str,
        )
        return jsonify(payload)
    except requests.exceptions.HTTPError as exc:
        return jsonify({"error": f"OpenWeatherMap request failed: {exc.response.status_code}"}), 502
    except Exception as exc:
        return jsonify({"error": f"Failed to fetch weather data: {str(exc)}"}), 500


@app.route("/api/weather/search", methods=["GET"])
def weather_search():
    """Geocode a city and return its weather data."""
    if not OWM_API_KEY:
        return jsonify({"error": "OpenWeatherMap API key is not configured."}), 500

    city_query = request.args.get("city", "").strip()
    if not city_query:
        return jsonify({"error": "Missing city parameter."}), 400

    geocoded = geocode_city(city_query)
    if not geocoded:
        return jsonify({"error": "City not found."}), 404

    timezone_str = find_timezone(geocoded["lat"], geocoded["lon"])
    try:
        payload = build_weather_payload(
            geocoded["lat"],
            geocoded["lon"],
            geocoded["city"],
            geocoded["country"],
            timezone_str,
        )
        return jsonify(payload)
    except requests.exceptions.HTTPError as exc:
        return jsonify({"error": f"OpenWeatherMap request failed: {exc.response.status_code}"}), 502
    except Exception as exc:
        return jsonify({"error": f"Failed to fetch weather data: {str(exc)}"}), 500


@app.route("/api/weather/suggest", methods=["GET"])
def weather_suggest():
    """Fast city autocomplete from the curated in-memory list."""
    query = request.args.get("query", "").strip().lower()
    if len(query) < 2:
        return jsonify([])

    matches = []
    for city_tuple in CITIES:
        name, country, lat, lon = city_tuple
        if query in name.lower():
            matches.append(_city_to_dict(city_tuple))
            if len(matches) >= 10:
                break
    return jsonify(matches)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
