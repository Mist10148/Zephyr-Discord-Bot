# Deployment Guide

Zephyr can run **locally on your machine** or be deployed to the cloud. This guide covers both workflows and explains which platforms are suitable for each component.

> **Important architectural note:**  
> The **Discord bot** must keep a persistent WebSocket connection to Discord's gateway. It cannot run on serverless platforms such as **Vercel** or **AWS Lambda**.  
> The **Flask website** is stateless HTTP and can run almost anywhere, including Vercel and Lambda.

---

## Table of contents

1. [Local development](#local-development)
2. [Docker](#docker)
3. [Render](#render)
4. [Vercel (website only)](#vercel-website-only)
5. [AWS (website only)](#aws-website-only)
6. [Environment variables](#environment-variables)
7. [Storage: local file vs Redis](#storage-local-file-vs-redis)
8. [Cross-platform binaries](#cross-platform-binaries)

---

## Local development

### Requirements

- **Python 3.13+**
- **FFmpeg** and **Opus** (see [Cross-platform binaries](#cross-platform-binaries))
- API keys: Discord bot token, OpenWeatherMap, Google Gemini, Spotify

### Setup

```bash
# 1. Clone
git clone https://github.com/Mist10148/Zephyr-Discord-Bot.git
cd Zephyr-Discord-Bot

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure secrets
copy .env.example .env          # Windows  (cp on macOS/Linux)
#   then edit .env and fill in your tokens/keys
```

### Run the bot

```bash
python run_bot.py
```

### Run the website

```bash
python run_web.py
```

Open **http://localhost:5000**.

To enable debug mode, set `FLASK_DEBUG=1` in your `.env` or shell.

---

## Docker

A single `Dockerfile` is included. It installs Linux FFmpeg and Opus automatically, so you do not need Windows binaries.

```bash
# Build
docker build -t zephyr .

# Run the website
docker run -p 5000:5000 --env-file .env zephyr

# Run the bot (override the default CMD)
docker run --env-file .env zephyr python run_bot.py
```

### Docker Compose (bot + website + Redis)

```bash
# Start everything
docker compose up -d

# View logs
docker compose logs -f bot

# Stop everything
docker compose down
```

`docker-compose.yml` starts:
- `redis` — shared AI settings storage
- `web` — Flask website on port 5000
- `bot` — Discord bot worker

Both services automatically receive `REDIS_URL=redis://redis:6379/0`, so AI settings are shared.

---

## Render

Render is the easiest cloud option because the repo already includes `render.yaml` (a Render Blueprint).

### One-click Blueprint

1. Push this repo to GitHub.
2. In Render, click **New +** → **Blueprint**.
3. Connect the repo. Render reads `render.yaml` and creates:
   - `zephyr-website` (Web Service)
   - `zephyr-bot` (Background Worker)
   - `zephyr-redis` (Redis instance)
4. Fill in the required environment variables in the Render dashboard:
   - `DISCORD_TOKEN`
   - `OPENWEATHER_API_KEY`
   - `GEMINI_API_KEY`
   - `SPOTIFY_CLIENT_ID`
   - `SPOTIFY_CLIENT_SECRET`

The website's live URL is automatically passed to the bot as `WEB_APP_URL`.

> **Note:** Render background workers require a paid plan (the Blueprint uses `plan: starter`).
> The web service can use Render's free tier, but it will spin down after inactivity.

### Manual services

If you prefer not to use the Blueprint:

1. Create a **Web Service** for the website:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn wsgi:app --bind 0.0.0.0:${PORT:-5000}`
2. Create a **Background Worker** for the bot:
   - Build command: `pip install -r requirements.txt`
   - Start command: `python run_bot.py`
3. Create a **Redis** instance and set `REDIS_URL` on the worker (optional but recommended).

---

## Vercel (website only)

The Flask website can be deployed to Vercel's serverless platform.

1. Install the Vercel CLI and log in:
   ```bash
   npm i -g vercel
   vercel login
   ```
2. Deploy:
   ```bash
   vercel
   ```
3. Add `OPENWEATHER_API_KEY` in the Vercel dashboard under **Settings → Environment Variables**.

> Do **not** try to deploy the Discord bot on Vercel — it requires a long-running process.

---

## AWS (website only)

The website can run on **AWS Lambda** + **API Gateway** using the included `aws_lambda_handler.py`.

### Deploy with the AWS CLI / console

1. Create a Lambda function with Python 3.13 runtime.
2. Upload a deployment package containing the project and dependencies, or use a Lambda layer.
3. Set the handler to `aws_lambda_handler.lambda_handler`.
4. Add `OPENWEATHER_API_KEY` as an environment variable.
5. Attach an API Gateway (HTTP or REST) trigger.

> As with Vercel, the Discord bot cannot run on Lambda. Run the bot on EC2, ECS/Fargate, or similar.

---

## Environment variables

| Variable | Required for | Description |
|----------|--------------|-------------|
| `DISCORD_TOKEN` | Bot | Discord bot token |
| `OPENWEATHER_API_KEY` | Bot + Website | OpenWeatherMap API key |
| `GEMINI_API_KEY` | Bot | Google Gemini API key |
| `SPOTIFY_CLIENT_ID` | Bot | Spotify app client ID |
| `SPOTIFY_CLIENT_SECRET` | Bot | Spotify app client secret |
| `FFMPEG_PATH` | Optional | Explicit path to FFmpeg |
| `WEB_APP_URL` | Optional | URL shown by `/use` |
| `FLASK_HOST` | Optional | Website bind host (default `0.0.0.0`) |
| `FLASK_PORT` | Optional | Website bind port (default `5000`) |
| `PORT` | Optional | Cloud-platform port override (overrides `FLASK_PORT`) |
| `REDIS_URL` | Optional | Redis connection string for shared settings |
| `SETTINGS_PATH` | Optional | Custom path for `settings.json` |
| `FLASK_DEBUG` | Optional | Set to `1` to enable Flask debug mode |

---

## Storage: local file vs Redis

AI settings (`/settings`, `/output`) and per-server/DM preferences are persisted.

| Mode | How it works | Best for |
|------|--------------|----------|
| **Local file** (default) | Reads/writes `settings.json` in the project root. | Local dev, single bot instance. |
| **Redis** | Stores the same JSON payload in Redis when `REDIS_URL` is set. | Cloud, multiple bot instances, ephemeral filesystems. |

If `REDIS_URL` is set but Redis is unreachable, the bot logs a warning and falls back to the local file.

---

## Cross-platform binaries

### FFmpeg

The bot resolves FFmpeg in this order:

1. `FFMPEG_PATH` environment variable
2. Bundled `ffmpeg/` folder (Windows `.exe` or plain `ffmpeg`)
3. `ffmpeg` on the system `PATH`

- **Windows:** drop `ffmpeg.exe`, `ffplay.exe`, `ffprobe.exe` into `ffmpeg/`, or install FFmpeg and add it to PATH.
- **Linux / macOS / Docker:** install FFmpeg via the package manager. It is automatically on PATH.

### Opus

The bot tries to load a platform-specific Opus library:

- **Windows:** bundled `libopus-0.x64.dll` or `libopus-0.dll`
- **Linux:** `libopus.so.0` or `libopus.so`
- **macOS:** `libopus.dylib`

Docker and most Linux distributions install `libopus0` automatically via the Dockerfile.
