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

Both services automatically receive `REDIS_URL=redis://redis:6379/0`, so AI settings are shared —
and, for the `web` service, so dashboard sessions work at all. Both now wait for Redis to pass a
healthcheck rather than merely start.

---

## Discord OAuth setup

Needed only for the dashboard. Skip it to serve the public weather site alone.

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and select the
   same application the bot uses. Go to **OAuth2**.
2. Under **Redirects**, add the callback for every origin you will sign in from:
   - production: `https://<your-host>/api/v1/auth/callback`
   - local Flask: `http://127.0.0.1:5000/api/v1/auth/callback`
   - local Vite dev server: `http://localhost:5173/api/v1/auth/callback`
3. Copy the **Client ID**, and **Reset Secret** to get a **Client Secret**.
4. Set `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` and `REDIS_URL`.

The redirect URI must **byte-match** a registered entry. A mismatch produces Discord's own
`invalid_redirect_uri` error page, not an application error — so if sign-in fails before ever
reaching Zephyr, check this first. By default the value is derived from `WEB_PUBLIC_URL` (or
Render's `RENDER_EXTERNAL_URL`); set `DISCORD_REDIRECT_URI` explicitly for a custom domain.

`WEB_APP_URL` is **not** used for OAuth. It is only the link `/use` prints, and its default is a
hardcoded ngrok host, so inheriting it would silently produce a redirect URI Discord rejects.

Startup validation is deliberately lenient in one direction and strict in the other: leaving all
three dashboard variables unset is a supported deployment, but setting only some of them raises at
boot with the list of what is missing.

### Two-terminal local development

The Vite dev server proxies `/api` to Flask, so run both and use `http://localhost:5173`:

```bash
python run_web.py                              # Flask on :5000
npm --prefix website/frontend run dev          # Vite on :5173
```

Set `DISCORD_REDIRECT_URI=http://localhost:5173/api/v1/auth/callback` for this setup, so the session
cookie lands on the origin the app is actually served from.

Only `/api` is proxied, which is why the OAuth endpoints live under `/api/v1/auth/*` — a top-level
`/auth/*` would 404 against the dev server and be swallowed by the SPA catch-all in production.

The service worker is not registered by the Vite dev server, so testing PWA behaviour means building
and serving through Flask instead:

```bash
npm --prefix website/frontend run build
python run_web.py                              # then use http://127.0.0.1:5000
```

### Dashboard routes

| Route | |
|---|---|
| `/login` | Sign in with Discord. Shows `?error=` codes from the callback in plain language |
| `/g` | Servers you administer, plus sign-out |
| `/g/:guildId` | Read-only settings overview for one server |

Signed-out visits to `/g` or `/g/:id` redirect to `/login?next=…` and return there afterwards. If no
OAuth application is configured, they redirect to `/login?error=not_configured` instead — a
deployment state rather than an error worth retrying.

---

## Database migrations

Alembic is available and correct, but **not automatic**. Nothing runs `alembic upgrade head` on
deploy: the web service runs two gunicorn workers that would race each other, and Render's free tier
has no release phase. Automating it is a hardening task.

Development and container startup still create tables via `create_all()` (`DB_AUTO_CREATE=1`), so a
fresh deployment needs no manual step. Migrations matter when the schema *changes*.

```bash
# Fresh database — build it from migrations alone
alembic upgrade head

# Existing deployed database, where ai_settings and app_state already exist
# because create_all() made them: adopt the baseline without re-creating anything
alembic stamp 0001
```

The `0001` baseline covers all four tables (`ai_settings`, `app_state`, `web_users`, `guilds`), so
`alembic downgrade base` is meaningful. Because `create_all()` and Alembic can drift, a
model-vs-migration check is on the hardening list; `tests/test_web_schema.py` currently asserts the
baseline matches the models.

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
   - `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET` — on the **web** service, for the dashboard
     (leave blank to deploy the public weather site only)

The website's live URL is automatically passed to the bot as `WEB_APP_URL`, and `REDIS_URL` is wired
into both the worker and the web service. The OAuth redirect URI derives from Render's own
`RENDER_EXTERNAL_URL`, so it needs no manual value unless you use a custom domain.

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
3. Create a **Redis** instance and set `REDIS_URL` on **both** services. It is optional but
   recommended for the worker (shared AI settings) and **required** on the web service for
   dashboard sessions.

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
