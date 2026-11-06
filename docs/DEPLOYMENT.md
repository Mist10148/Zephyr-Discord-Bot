# Deployment Guide

Zephyr can run **locally on your machine** or be deployed to the cloud. This guide covers both workflows and explains which platforms are suitable for each component.

> **Important architectural note:**  
> The **Discord bot** must keep a persistent WebSocket connection to Discord's gateway, and the
> **website** keeps a persistent Redis connection for dashboard sessions. Neither runs on
> serverless platforms such as **Vercel** or **AWS Lambda** — see
> [Serverless is not supported](#serverless-is-not-supported).

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

### Docker Compose (Postgres + Redis + bot + website)

```bash
# Start everything
docker compose up -d

# View logs
docker compose logs -f bot

# Stop everything
docker compose down
```

`docker-compose.yml` starts:
- `postgres` — the database (AI settings, playlists, weather subscriptions, audit log…)
- `redis` — dashboard sessions and the bot ↔ web bridge
- `web` — Flask website on port 5000
- `bot` — Discord bot worker

Both `web` and `bot` receive `DATABASE_URL` pointing at the `postgres` service and
`REDIS_URL=redis://redis:6379/0`, and wait for both to pass a healthcheck rather than merely start.

Compose does **not** run migrations. Tables are created on first start (`DB_AUTO_CREATE=1`); after
pulling a change that adds a migration, run it once:

```bash
docker compose run --rm web alembic upgrade head
```

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

Startup validation is deliberately lenient in one direction and strict in the other: leaving
`DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET` unset is a supported deployment, but setting only one
of them — or both without `REDIS_URL` — raises at boot with the list of what is missing. `REDIS_URL`
on its own does not count as half-configuring the dashboard.

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
| `/g/:guildId/settings` | Default volume, DJ role and music channels (applied by the bot); prefix, locale and timezone (stored, not yet used by the bot) |
| `/g/:guildId/ai` | AI personas, per-channel memory, usage |
| `/g/:guildId/audit` | Who changed what, from Discord or the dashboard |
| `/account/ai-history` | Your private DM history with the bot |
| `/weather` | Public forecast page |
| `/commands` | Searchable list of every command |
| `/settings` | Appearance (theme) preferences for this browser |
| `/kitchen-sink` | Component gallery for the design system |

Signed-out visits to `/g` or `/g/:id` redirect to `/login?next=…` and return there afterwards. If no
OAuth application is configured, they redirect to `/login?error=not_configured` instead — a
deployment state rather than an error worth retrying.

---

## Database migrations

Alembic runs automatically **only on the Render Blueprint**. `render.yaml`'s web service carries a
`preDeployCommand: alembic upgrade head`, which Render runs **once** between build and release — a
single process, so the two gunicorn workers never race it, and it stands in for the release phase the
free tier does not otherwise have. Docker, Docker Compose, the `Procfile` and manually created Render
services do not run it; do that yourself after deploying a change that adds a revision.

Development and container startup still create tables via `create_all()` (`DB_AUTO_CREATE=1`), so a
fresh deployment needs no manual step either. Migrations matter when the schema *changes*; on Render
the pre-deploy hook applies them before any worker serves traffic. To run one by hand (a non-Render
host, or a recovery):

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
| `0004` | `ai_conversations`, `ai_messages`, `personas` |
| `0005` | AI history management (`ai_message_revisions`, conversation metadata) |
| `0006` | `ai_conversation_labels`, `ai_conversation_annotations` |
| `0007` | DM conversation ownership (`ai_conversations.owner_id`) |
| `0008` | Data only: assigns legacy DM conversations to their recipients |

`alembic downgrade base` is meaningful at every step. Because `create_all()` and Alembic can drift,
`tests/test_web_schema.py` asserts the two agree — `test_migrations_and_create_all_agree` builds a
database each way and compares tables and columns in both directions, so a model change that skips a
migration fails CI rather than production.

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
   - `zephyr-db` (Postgres database, wired into both services as `DATABASE_URL`)
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

> **Music and TTS on Render:** the Blueprint's worker uses Render's native Python runtime, which has
> no FFmpeg, no Opus and no JavaScript runtime (yt-dlp needs one for YouTube). The Docker image
> installs all three. If you need voice features, deploy the bot worker from the `Dockerfile`
> (`runtime: docker`, start command `python run_bot.py`) instead.

### Manual services

If you prefer not to use the Blueprint:

1. Create a **Web Service** for the website:
   - Build command: `npm --prefix website/frontend ci && npm --prefix website/frontend run build && pip install -r requirements.txt`
     (without the SPA build every page answers `503 spa_not_built`), with `NODE_VERSION=22`
   - Pre-deploy command: `alembic upgrade head`
   - Start command: `gunicorn wsgi:app --bind 0.0.0.0:${PORT:-5000}`
2. Create a **Background Worker** for the bot:
   - Build command: `pip install -r requirements.txt`
   - Start command: `python run_bot.py`
3. Create a **Redis** instance and set `REDIS_URL` on **both** services. It is **required** on the
   web service for dashboard sessions, and on both for the bot ↔ web bridge.
4. Create a **Postgres** database and set `DATABASE_URL` on **both** services. Without it each
   service falls back to its own throwaway SQLite file.

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
| Check the **CSP** | [`website/security.py`](../website/security.py) allows images from `cdn.discordapp.com`, `*.ytimg.com`, `*.ggpht.com` and `*.scdn.co`. Adding another image host means updating it, or those images are silently blocked |
| Confirm `/health` | Returns `{"status": "ok"}`; point your platform's health check at it |
| Build the SPA | The Render Blueprint and the Dockerfile do this for you; a manual Render service or Heroku does not. A missing build makes the site answer `503 spa_not_built` rather than failing quietly |

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
| `OPENWEATHER_API_KEY` | Bot | OpenWeatherMap API key (the website uses Open-Meteo and never reads it) |
| `GEMINI_API_KEY` | Bot | Google Gemini API key |
| `SPOTIFY_CLIENT_ID` | Bot + Website | Spotify app client ID (the website uses it for playlist import only) |
| `SPOTIFY_CLIENT_SECRET` | Bot + Website | Spotify app client secret |
| `DATABASE_URL` | Both | Postgres/SQLite URL. Defaults to the SQLite file `data/zephyr.db`, which is lost on ephemeral filesystems |
| `REDIS_URL` | Both | Dashboard sessions and the bot ↔ web bridge (`REDISCLOUD_URL` is an alias) |
| `STORAGE_BACKEND` | Optional | AI-settings store: `auto` (default), `database`, `redis`, `file` |
| `DB_AUTO_CREATE` / `DB_ECHO` | Optional | Create missing tables at startup (default on) / log SQL (default off) |
| `FFMPEG_PATH` | Optional | Explicit path to FFmpeg |
| `WEB_APP_URL` | Optional | URL shown by `/use` |
| `FLASK_HOST` | Optional | Website bind host (default `0.0.0.0`) |
| `FLASK_PORT` | Optional | Website bind port (default `5000`) |
| `PORT` | Optional | Cloud-platform port override (overrides `FLASK_PORT`) |
| `SETTINGS_PATH` | Optional | `settings.json` fallback path, used only if the chosen store is unreachable at startup |
| `FLASK_DEBUG` | Optional | `1` enables the Werkzeug debugger (binds to `127.0.0.1` unless `FLASK_HOST` is set) |
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | Dashboard | OAuth2 credentials from the bot's own Discord application |
| `WEB_PUBLIC_URL` | Dashboard | Public origin; Render supplies `RENDER_EXTERNAL_URL` |
| `DISCORD_REDIRECT_URI` | Dashboard | Defaults to `<WEB_PUBLIC_URL>/api/v1/auth/callback` |
| `SESSION_TTL_SECONDS` / `SESSION_MAX_AGE_SECONDS` | Dashboard | Sliding session lifetime (7 days) and absolute cap (30 days) |
| `AUTH_COOKIE_SECURE` | Dashboard | Auto-on for `https://` origins |
| `AUTH_COOKIE_NAME` / `CSRF_COOKIE_NAME` | Dashboard | Cookie names (default `zephyr_session` / `zephyr_csrf`) |
| `GUILDS_FRESH_SECONDS` | Dashboard | How long a session's guild list is trusted (default 1 hour) |
| `TRUST_PROXY_HEADERS` | Dashboard | Trust `X-Forwarded-*`; auto-on when `RENDER` is set |
| `DISCORD_INVITE_PERMISSIONS` | Dashboard | Permissions bitfield for the "Add Zephyr" link |

---

## Storage for AI settings

AI settings (`/settings`, `/output`) and per-server/DM preferences are persisted. With
`STORAGE_BACKEND=auto` (the default) the bot picks the first that applies:

| Mode | When | Notes |
|------|------|-------|
| **Database** | `DATABASE_URL` is set | Tables `ai_settings` / `app_state`. Best for the cloud and for several bot instances |
| **Redis** | no `DATABASE_URL`, but `REDIS_URL` is set | One JSON document under `zephyr:settings` |
| **Local SQLite** (default) | neither is set | `data/zephyr.db` in the project. Lost on ephemeral filesystems |
| **File** | only if the chosen backend cannot be reached at startup | `settings.json` (or `SETTINGS_PATH`) |

Set `STORAGE_BACKEND` to `database`, `redis` or `file` to force one. `python -m
scripts.import_settings` migrates an existing `settings.json` into the database. If a backend fails
to *load* at startup, the bot refuses to save settings until a load succeeds, so a brief outage can
never overwrite what is stored.

---

## Cross-platform binaries

### FFmpeg

The bot resolves FFmpeg in this order:

1. An `ffmpeg/` folder inside a PyInstaller build (only when running as a frozen executable)
2. `FFMPEG_PATH` environment variable
3. Bundled `ffmpeg/` folder (Windows `.exe` or plain `ffmpeg`)
4. `ffmpeg` on the system `PATH`

- **Windows:** drop `ffmpeg.exe`, `ffplay.exe`, `ffprobe.exe` into `ffmpeg/`, or install FFmpeg and add it to PATH.
- **Linux / macOS / Docker:** install FFmpeg via the package manager. It is automatically on PATH.

### Opus

The bot tries to load a platform-specific Opus library:

- **Windows:** bundled `libopus-0.x64.dll` or `libopus-0.dll`
- **Linux:** `libopus.so.0` or `libopus.so`
- **macOS:** `libopus.dylib`

Docker and most Linux distributions install `libopus0` automatically via the Dockerfile.
