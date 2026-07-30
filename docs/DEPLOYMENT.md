# Deployment Guide

Zephyr can run **locally on your machine** or be deployed to the cloud. This guide covers both workflows and explains which platforms are suitable for each component.

> **Important architectural note:**  
> The **Discord bot** must keep a persistent WebSocket connection to Discord's gateway. It cannot run on serverless platforms such as **Vercel** or **AWS Lambda**.  
> The **Flask website** is stateless HTTP and can run almost anywhere, including Vercel and Lambda.

---

## Table of contents

1. [Local development](#local-development)
2. [Docker](#docker)
3. [Discord OAuth setup](#discord-oauth-setup)
4. [Database migrations](#database-migrations)
5. [Render](#render)
6. [Serverless is not supported](#serverless-is-not-supported)
7. [Hosting checklist](#hosting-checklist)
8. [Environment variables](#environment-variables)
9. [Storage: local file vs Redis](#storage-local-file-vs-redis)
10. [Cross-platform binaries](#cross-platform-binaries)

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

`WEB_APP_URL` is **not** used for OAuth. It is only the link `/use` prints, and it may legitimately
point somewhere else entirely — so the redirect URI is derived independently. Keep the two separate.

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
| `/g/:guildId` | Overview for one server, linking to the pages below |
| `/g/:guildId/music` | Now playing, queue, transport controls and the playlist editor |
| `/g/:guildId/weather-alerts` | Weather subscriptions, with a preview |
| `/g/:guildId/settings` | Prefix, locale, timezone, default volume, DJ role, music channels |

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

# ...then bring it up to date with the later revisions
alembic upgrade head
```

Revisions so far:

| Revision | Tables |
|---|---|
| `0001` | `ai_settings`, `app_state`, `web_users`, `guilds` |
| `0002` | `playlists`, `playlist_tracks`, `audit_log` |
| `0003` | `weather_subs`, `bot_users` |

`alembic downgrade base` is meaningful at every step. Because `create_all()` and Alembic can drift,
a model-vs-migration check is on the hardening list; `tests/test_web_schema.py` currently asserts the
baseline matches the models.

### The bot ↔ web bridge

The dashboard's music remote and its channel pickers need **both** processes plus Redis. The bot
publishes `zephyr:presence` (30s TTL) and `zephyr:player:{guild_id}` (60s TTL) and listens on
`zephyr:cmd`; the web tier reads the snapshots and sends commands, waiting up to five seconds for a
reply on `zephyr:res:{id}`.

The TTLs are the design. If the bot is down, the keys expire and the dashboard says so instead of
showing stale state, and `POST /api/v1/guilds/:id/player/*` answers `504 bot_unreachable` rather
than hanging. Without `REDIS_URL` the same endpoints answer `503 bridge_not_configured`, `/status`
reports the bot offline, and everything that does not need the bot keeps working.

Permissions are always re-checked by the bot against its live Discord cache, never trusted from the
browser: if a DJ role is configured you need it (or Manage Server), and if one is not you need to be
in the voice channel the bot is in.

### Weather subscriptions

The `weather_alerts` cog runs two loops in the **bot** process, so a website-only deployment posts
nothing. Digests are claimed transactionally (`FOR UPDATE SKIP LOCKED` on Postgres), so running two
bot instances will not double-post; severe and class-suspension watches are deduplicated by
fingerprint instead, and only record a run once something was actually sent.

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

## Serverless is not supported

Vercel and AWS Lambda were previously documented for the website, via `vercel.json`,
`vercel_handler.py` and `aws_lambda_handler.py`. **Those files were deleted and the platforms are no
longer supported.** The instructions were left behind by mistake and are now removed.

Neither half of Zephyr fits a serverless model:

- The **bot** needs a persistent process for its Discord gateway connection.
- The **website** now needs a persistent Redis connection for dashboard sessions, and a request-scoped
  function cannot hold one.

Use Docker, Render, Heroku, or a VM (EC2 / ECS / Fargate). For a website-only deployment, run the
Flask app under gunicorn on any of those.

---

## Hosting checklist

Worth walking once before pointing users at a deployment.

| | Why |
|---|---|
| Serve over **HTTPS** | Session cookies get `Secure` automatically for `https://` origins, and HSTS is only sent from one. Over plain http the dashboard works but the session cookie is not protected in transit |
| Set **`WEB_APP_URL`** | It has no default. `/use` reports that it is unconfigured rather than linking somewhere wrong |
| Set **`REDIS_URL`** on the web service | Required for the dashboard. Without it the site serves the public weather pages only |
| Register the **OAuth redirect** | Must byte-match; see [Discord OAuth setup](#discord-oauth-setup) |
| Run **`alembic upgrade head`** | Only for an existing database; a fresh one is created automatically. See [Database migrations](#database-migrations) |
| Check the **CSP** | [`website/security.py`](../website/security.py) allows images from `cdn.discordapp.com`, `i.ytimg.com` and `i.scdn.co`. Adding another image host means updating it, or those images are silently blocked |
| Confirm `/health` | Returns `{"status": "ok"}`; point your platform's health check at it |
| Build the SPA | Render and Docker do this for you. A missing build makes the site answer `503 spa_not_built` rather than failing quietly |

The website sends `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, `Permissions-Policy` and `Cross-Origin-Opener-Policy` on every response, plus
`Strict-Transport-Security` when the public origin is https.

### App icons

The site is installable as a PWA. Its icons live in `website/frontend/public/icons/` and are
generated from the same design tokens as `theme.css`, so the installed icon matches the app:

```bash
python -m scripts.generate_icons                  # the generated wordmark
python -m scripts.generate_icons --source roxy.jpg  # or use an image of your own
```

Re-run it after changing the palette and commit the result. Both `any` and `maskable` purposes are
published: with only `any`, Android crops the artwork with its own mask and clips the mark. The
192px entries are what Chrome checks for installability, so removing them silently disables the
install prompt.

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
