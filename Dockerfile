# Zephyr Discord bot + Flask website
# Build:  docker build -t zephyr .
# Run web: docker run -p 5000:5000 --env-file .env zephyr
# Run bot: docker run --env-file .env zephyr python run_bot.py

FROM python:3.13-slim

# Install system dependencies required by the bot and website.
# libopus0 provides Opus voice support; ffmpeg handles audio decoding.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency list first for better Docker layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application.
COPY . .

# Cloud platforms provide PORT; default to 5000 for local Docker runs.
EXPOSE 5000

# Default to the website served by Gunicorn. Override this command to run the Discord bot.
# The shell form lets us honour the PORT env var set by cloud platforms.
CMD gunicorn wsgi:app --bind 0.0.0.0:${PORT:-5000}
