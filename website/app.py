"""Flask weather website.

Ported 1:1 from the original Main.py. The OpenWeatherMap key and endpoints now
come from ``zephyr.config`` (loaded from .env) instead of being hardcoded.
"""

from flask import Flask, render_template, request, jsonify
import requests
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
from datetime import datetime
import pytz

from zephyr.config import API_KEY, CURRENT_URL, FORECAST_URL

app = Flask(__name__)


@app.route('/')
def home():
    return render_template("index.html")


@app.route('/weather', methods=["POST"])
def get_weather():
    city = request.form['city']
    geolocator = Nominatim(user_agent="weather_app")
    location = geolocator.geocode(city)

    if location:
        latitude, longitude = location.latitude, location.longitude
        tf = TimezoneFinder()
        timezone = tf.timezone_at(lng=longitude, lat=latitude)

        # Fetch current weather
        current_req = f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric"
        current_data = requests.get(current_req).json()

        # Fetch forecast
        forecast_req = f"{FORECAST_URL}?appid={API_KEY}&lat={latitude}&lon={longitude}&units=metric"
        forecast_data = requests.get(forecast_req).json()

        # Extract data
        forecast_list = forecast_data.get('list', [])
        daily_forecast = []
        days_to_fetch = 4  # Fetch 4 days of forecast
        time_interval = 8  # The API gives data every 3 hours, so 8 entries = 1 day

        for i in range(0, days_to_fetch):
            day_start = i * time_interval
            day_end = day_start + time_interval
            day_data = forecast_list[day_start:day_end]

            if day_data:
                # Default values to handle missing data
                day_temp = day_data[4]["main"].get("temp", "N/A") if len(day_data) > 4 else "N/A"
                day_desc = day_data[4]["weather"][0].get("description", "N/A") if len(day_data) > 4 else "N/A"
                day_humidity = day_data[4]["main"].get("humidity", "N/A") if len(day_data) > 4 else "N/A"
                day_pressure = day_data[4]["main"].get("pressure", "N/A") if len(day_data) > 4 else "N/A"
                day_wind = day_data[4]["wind"].get("speed", "N/A") if len(day_data) > 4 else "N/A"

                night_temp = day_data[-1]["main"].get("temp", "N/A")
                night_desc = day_data[-1]["weather"][0].get("description", "N/A")
                night_humidity = day_data[-1]["main"].get("humidity", "N/A")
                night_pressure = day_data[-1]["main"].get("pressure", "N/A")
                night_wind = day_data[-1]["wind"].get("speed", "N/A")

                dt = datetime.utcfromtimestamp(day_data[0]["dt"])
                weekday_name = dt.strftime('%A')

                daily_forecast.append({
                    "date": dt.strftime('%Y-%m-%d'),
                    "weekday": weekday_name,
                    "day_temp": day_temp,
                    "day_desc": day_desc,
                    "day_humidity": day_humidity,
                    "day_pressure": day_pressure,
                    "day_wind": day_wind,
                    "night_temp": night_temp,
                    "night_desc": night_desc,
                    "night_humidity": night_humidity,
                    "night_pressure": night_pressure,
                    "night_wind": night_wind
                })

        return jsonify({
            "current": {
                "temp": current_data["main"].get("temp", "N/A"),
                "desc": current_data["weather"][0].get("description", "N/A"),
                "humidity": current_data["main"].get("humidity", "N/A"),
                "pressure": current_data["main"].get("pressure", "N/A"),
                "wind_speed": current_data["wind"].get("speed", "N/A"),
            },
            "forecast": daily_forecast,
            "timezone": timezone,
        })
    else:
        return jsonify({"error": "City not found"}), 404
