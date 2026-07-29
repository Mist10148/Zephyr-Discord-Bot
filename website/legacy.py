"""Temporary Jinja weather routes retained until the SPA ships."""

from datetime import datetime

import pytz
import requests
from flask import Blueprint, jsonify, render_template, request
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder

from zephyr.config import API_KEY, CURRENT_URL, FORECAST_URL

legacy = Blueprint("legacy", __name__)


@legacy.get("/")
def home():
    return render_template("index.html")


@legacy.post("/weather")
def get_weather():
    city = request.form["city"]
    location = Nominatim(user_agent="weather_app").geocode(city)
    if not location:
        return jsonify({"error": "City not found"}), 404
    latitude, longitude = location.latitude, location.longitude
    timezone = TimezoneFinder().timezone_at(lng=longitude, lat=latitude)
    current_data = requests.get(f"{CURRENT_URL}?appid={API_KEY}&q={city}&units=metric").json()
    forecast_data = requests.get(
        f"{FORECAST_URL}?appid={API_KEY}&lat={latitude}&lon={longitude}&units=metric"
    ).json()
    daily_forecast = []
    for index in range(4):
        day_data = forecast_data.get("list", [])[index * 8:(index + 1) * 8]
        if not day_data:
            continue
        daytime = day_data[4] if len(day_data) > 4 else {"main": {}, "weather": [{}], "wind": {}}
        nighttime = day_data[-1]
        dt = datetime.utcfromtimestamp(day_data[0]["dt"])
        daily_forecast.append({
            "date": dt.strftime("%Y-%m-%d"), "weekday": dt.strftime("%A"),
            "day_temp": daytime["main"].get("temp", "N/A"), "day_desc": daytime["weather"][0].get("description", "N/A"),
            "day_humidity": daytime["main"].get("humidity", "N/A"), "day_pressure": daytime["main"].get("pressure", "N/A"), "day_wind": daytime["wind"].get("speed", "N/A"),
            "night_temp": nighttime["main"].get("temp", "N/A"), "night_desc": nighttime["weather"][0].get("description", "N/A"),
            "night_humidity": nighttime["main"].get("humidity", "N/A"), "night_pressure": nighttime["main"].get("pressure", "N/A"), "night_wind": nighttime["wind"].get("speed", "N/A"),
        })
    return jsonify({"current": {"temp": current_data["main"].get("temp", "N/A"), "desc": current_data["weather"][0].get("description", "N/A"), "humidity": current_data["main"].get("humidity", "N/A"), "pressure": current_data["main"].get("pressure", "N/A"), "wind_speed": current_data["wind"].get("speed", "N/A")}, "forecast": daily_forecast, "timezone": timezone})
