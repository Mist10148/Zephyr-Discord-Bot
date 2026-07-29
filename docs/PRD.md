# Zephyr — Product Requirements Document (PRD)

**Version:** 1.2  
**Last updated:** 2026-07-30  
**Owner:** Zephyr Discord Bot project  

> **In planning:** a React web dashboard with Discord OAuth, plus four bot feature tracks
> (weather subscriptions, persistent playlists, AI summarization, database-backed settings).
> Architecture, data model, API surface, and phased delivery live in
> [`WEB_DASHBOARD_PLAN.md`](WEB_DASHBOARD_PLAN.md). Sections 6–11 below describe the
> **currently shipped** product.

---

## 1. Overview

Zephyr is a modular, multi-purpose Discord bot built for community servers. It bundles a weather service, a Groovy-style music player, a Google Gemini AI chat companion, text-to-speech, and a small Flask companion website — all organized into clean, self-contained cogs.

The bot is written in Python 3.13 using `discord.py`, exposes **64 slash commands** (including aliases) and **13 prefix commands**, and runs on Windows, macOS, or Linux. It can be run locally, in Docker, or deployed to cloud platforms such as Render, Heroku, AWS, and Vercel.

---

## 2. Goals & Non-Goals

### Goals
- Provide fast, accurate weather information (current, forecast, air quality, typhoon alerts).
- Stream music from YouTube and Spotify inside voice channels.
- Offer an AI chat companion with per-server/DM preferences and image generation.
- Add lightweight voice/TTS utilities.
- Expose every feature through intuitive slash commands, with selected weather commands also available as prefix commands.
- Keep the codebase modular so features can be added, removed, or updated independently.

### Non-Goals
- Not a general-purpose bot moderation/admin suite (no ban/kick/role management).
- Not a persistent music playlist database.
- Not a replacement for official weather/pagasa alerts; class-suspension forecast is advisory only.

---

## 3. Target Users

- **Primary:** Discord server admins and members in the Philippines, especially Iloilo City users who need local weather and class-suspension forecasts.
- **Secondary:** Any Discord community that wants music, AI chat, and TTS in one bot.

---

## 4. Tech Stack

| Area | Technology |
|------|------------|
| Language | Python 3.13 |
| Discord framework | `discord.py` (with voice / PyNaCl) |
| AI engine | `google-genai` — Google Gemini |
| Music extraction | `yt-dlp` |
| Spotify metadata | `spotipy` (Spotify Web API) |
| Text-to-speech | `gTTS` |
| Audio codec | FFmpeg, Opus (`libopus-0.x64.dll` on Windows, system libs on Linux/macOS) |
| Weather data | Open-Meteo (primary), OpenWeatherMap (fallback) |
| Website | Flask, `geopy`, `timezonefinder`, `pytz`, Swiper.js |
| Configuration | `python-dotenv` (`.env`) + `settings.json` or Redis |
| Utilities | `aiohttp`, `requests`, `async-timeout` |
| Cloud deployment | Docker, Gunicorn, Redis, Render Blueprint, Vercel, AWS Lambda |

---

## 5. Architecture

```
project-root/
├── run_bot.py              # Discord bot entry point
├── run_web.py              # Flask website entry point (local dev)
├── wsgi.py                 # Production WSGI entry point
├── aws_lambda_handler.py   # AWS Lambda entry point (website)
├── vercel_handler.py       # Vercel serverless entry point (website)
├── Dockerfile              # Container image for bot or website
├── docker-compose.yml      # Local orchestration: Redis + bot + website
├── Procfile                # Render/Heroku process definitions
├── render.yaml             # Render Blueprint
├── vercel.json             # Vercel routing config
├── requirements.txt
├── .env.example            # Secret/template file
├── settings.json           # Persisted per-context AI settings (local file)
├── ffmpeg/                 # FFmpeg binaries (not committed)
├── libopus-0.x64.dll       # Windows Opus codec
├── zephyr/                 # Bot package
│   ├── config.py           # Loads .env + constants
│   ├── client.py           # Bot subclass, cog loading, slash sync, events
│   ├── core/               # opus_loader, ffmpeg resolver
│   ├── cogs/               # Feature cogs
│   │   ├── weather.py
│   │   ├── music.py
│   │   ├── voice_tts.py
│   │   ├── chat.py
│   │   └── help.py
│   ├── services/           # AI engine + portable storage
│   │   ├── gemini.py
│   │   └── storage.py
│   └── utils/              # Shared helpers
│       ├── weather_utils.py
│       ├── pagination.py
│       ├── help_data.py
│       └── time_utils.py
└── website/                # Flask app
    ├── app.py
    └── templates/index.html
```

### Cog loading
`zephyr/client.py` loads every cog in `EXTENSIONS` during `setup_hook()`, then syncs the slash command tree. Loading failures are logged but do not crash the bot.

### Entry points
- **Bot (local):** `python run_bot.py` → validates config → runs `ZephyrBot`.
- **Website (local):** `python run_web.py` → validates web config → runs Flask on `FLASK_HOST:FLASK_PORT`.
- **Website (production WSGI):** `gunicorn wsgi:app`.
- **Website (Vercel):** `vercel_handler.py` exposes the Flask `app` callable.
- **Website (AWS Lambda):** `aws_lambda_handler.lambda_handler` proxies requests via `awsgi`.
- **Container:** `Dockerfile` installs Linux FFmpeg/Opus; default command runs the website; override to run the bot.

---

## 6. Features

### 6.1 Weather
- **Current conditions:** temperature, description, humidity, wind, pressure, precipitation, air quality.
- **3-day forecast:** clean daily pages with high/low temperature, feels-like high/low, rain chance, and max wind.
- **Air quality:** AQI and pollutant details.
- **Typhoon alerts:** one-call alerts for Iloilo City.
- **Class suspension forecast:** predicts whether classes are likely to be suspended using feels-like/apparent temperature.
- **Data sources:** Open-Meteo is the primary source; OpenWeatherMap is used as a transparent fallback if Open-Meteo fails.
- **Command formats:** every weather command works as a slash command; many also work as prefix commands.

### 6.2 Music
- **Playback:** play, playskip, playnext, search-and-pick (`/msearch`), now playing, pause/resume/stop, seek/forward/rewind, lyrics.
- **Sources:** YouTube video/playlist URLs, search queries, Spotify tracks/playlists/albums (resolved to YouTube audio).
- **Queue management:** view queue, skip (vote-based), jump, move, remove, clear, shuffle, loop modes.
- **Voice connection:** join, summon, leave, disconnect, 24/7 mode.
- **Audio effects:** volume, bass boost, pitch, nightcore, vaporwave, slowed, reverb, slowed+reverb, 16D, reset effects.

### 6.3 AI Chat & TTS
- **Gemini chat:** `/prompt` with text, image, or `.txt` attachments; mention/reply/DM the bot to chat.
- **Customization:** choose AI model and response format (embed / text / `.txt` file) via `/settings` and `/output`.
- **Rate limits:** local per-model RPM/TPM/RPD tracking shown with `/token`.
- **Image generation:** `/image-gen` (Gemini) and `/generate` (optional external hook).
- **TTS:** `/say` speaks in a voice channel; `/language` changes the TTS language.

### 6.4 Help System
- Centralized command registry in `zephyr/utils/help_data.py`.
- `/help` — paginated overview of all commands with a table-of-contents page.
- `/helpmusic`, `/helpchat`, `/helpweather` — filtered category views.
- Consistent embed formatting across every help command.

### 6.5 Flask Website
- Home panel shows Iloilo City's current weather and 4-day forecast.
- City search returns current conditions + day/night forecast entries.
- Uses OpenWeatherMap + Nominatim geocoding.

---

## 7. Command Inventory

### 7.1 Slash commands (64 total, including aliases)

| Category | Commands |
|----------|----------|
| **Weather** | `/weather`, `/forecast`, `/temperature`, `/description`, `/humidity`, `/pressure`, `/windspeed`, `/air`, `/precipitation`, `/typhoon`, `/class`, `/search`, `/use`, `/helpweather`, `/ping` |
| **Music — Playback** | `/play`, `/playskip`, `/playnext`, `/msearch`, `/now`, `/np`, `/pause`, `/resume`, `/stop`, `/seek`, `/forward`, `/rewind`, `/lyrics` |
| **Music — Queue** | `/queue`, `/skip`, `/jump`, `/move`, `/remove`, `/clear`, `/shuffle`, `/loop`, `/loopqueue` |
| **Music — Effects & Audio** | `/volume`, `/bassboost`, `/bass_boost`, `/pitch`, `/nightcore`, `/vaporwave`, `/slowed`, `/reverb`, `/slownrev`, `/16d`, `/reset_effects` |
| **Music — Voice & Connection** | `/join`, `/summon`, `/leave`, `/disconnect`, `/247` |
| **Chat & AI** | `/prompt`, `/settings`, `/output`, `/token`, `/image-gen`, `/generate` |
| **TTS & Voice** | `/say`, `/language`, `/disconnect` |
| **Help** | `/help`, `/helpmusic`, `/helpchat`, `/helpweather` |

### 7.2 Prefix commands (13 total)

| Command | Description |
|---------|-------------|
| `temperature <city>` | Current temperature |
| `description <city>` | Weather description |
| `humidity <city>` | Humidity |
| `pressure <city>` | Atmospheric pressure |
| `windspeed <city>` | Wind speed |
| `use` | Web app link |
| `helpweather` | Weather command help (prefix) |
| `precipitation <city>` | Rain/snow details |
| `typhoon` | Typhoon alert for Iloilo |
| `air <city>` | Air quality |
| `weather <city>` | Current weather |
| `forecast <city>` | Forecast (legacy prefix view) |
| `search <city>` | Quick weather lookup |

---

## 8. External APIs & Data Sources

| Service | Used For | Auth |
|---------|----------|------|
| **Open-Meteo** | Daily forecast, current apparent temperature, geocoding | None |
| **OpenWeatherMap** | Legacy/current weather, forecast fallback, website data, prefix weather commands | API key |
| **Google Gemini** | AI chat, image generation | API key |
| **Spotify Web API** | Track/playlist/album metadata lookup | Client ID + Secret |
| **YouTube** | Audio streaming (via `yt-dlp`) | None |
| **Discord** | Bot platform | Bot token |
| **Nominatim** | Website city geocoding | None |

---

## 9. Configuration

All secrets live in `.env` (see `.env.example`).

| Variable | Required For | Description |
|----------|--------------|-------------|
| `DISCORD_TOKEN` | Bot | Discord bot token |
| `OPENWEATHER_API_KEY` | Bot fallback + Website | OpenWeatherMap API key |
| `GEMINI_API_KEY` | Bot | Google Gemini API key |
| `SPOTIFY_CLIENT_ID` | Bot | Spotify app client ID |
| `SPOTIFY_CLIENT_SECRET` | Bot | Spotify app client secret |
| `FFMPEG_PATH` | Optional | Path to FFmpeg |
| `WEB_APP_URL` | Optional | URL shown by `/use`. Not the OAuth origin |
| `FLASK_HOST` / `FLASK_PORT` | Optional | Website bind address |
| `PORT` | Optional | Cloud-platform port override |
| `REDIS_URL` | Optional + **Dashboard** | Shared AI settings storage, and dashboard sessions |
| `SETTINGS_PATH` | Optional | Custom `settings.json` path |
| `FLASK_DEBUG` | Optional | Enable Flask debug mode |
| `DATABASE_URL` | Optional | Postgres/SQLite URL (defaults to `data/zephyr.db`) |
| `DISCORD_CLIENT_ID` | Dashboard | OAuth2 client ID (same application as the bot) |
| `DISCORD_CLIENT_SECRET` | Dashboard | OAuth2 client secret |
| `WEB_PUBLIC_URL` | Optional | Public dashboard origin; Render supplies `RENDER_EXTERNAL_URL` |
| `DISCORD_REDIRECT_URI` | Optional | Defaults to `<WEB_PUBLIC_URL>/api/v1/auth/callback` |
| `SESSION_TTL_SECONDS` / `SESSION_MAX_AGE_SECONDS` | Optional | Sliding lifetime and hard cap |
| `AUTH_COOKIE_SECURE` | Optional | Auto-on for `https://` origins |
| `GUILDS_FRESH_SECONDS` | Optional | Guild-list freshness window |
| `TRUST_PROXY_HEADERS` | Optional | Trust `X-Forwarded-*`; auto-on under Render |

Leaving all three dashboard variables unset serves the public weather site only. Setting some but not
all of them raises at startup.

`settings.json` stores per-server/DM AI preferences locally; when `REDIS_URL` is set, the same JSON payload is stored in Redis for shared cloud state.

---

## 10. Permissions & Intents

The bot requests `discord.Intents.all()` and requires these privileged intents in the Discord Developer Portal:
- **Message Content** — for prefix commands and mention/reply AI chat.
- **Server Members** — for member-related features.
- **Presence** — optional, for richer presence data.

Additional permissions needed at invite time:
- Send Messages, Embed Links, Attach Files
- Connect, Speak (voice/music/TTS)
- Use Slash Commands

---

## 11. Error Handling & Fallbacks

- **Weather forecast/class:** Open-Meteo is tried first; if geocoding or the API call fails, the bot falls back to OpenWeatherMap and notes it in the embed.
- **Cog loading:** a failing cog is logged but does not prevent other cogs from loading.
- **Slash sync:** failures are logged; the bot still comes online.
- **AI generation:** `/generate` gracefully reports unavailability if the optional `image_generator` module is missing.

---

## 12. Roadmap / TODO

Detailed scope per phase: [`WEB_DASHBOARD_PLAN.md`](WEB_DASHBOARD_PLAN.md) §6.

**Committed — phased build (tracked as tasks #1–#8)**

- [x] Support local and cloud deployment (Docker, Render, Vercel, AWS).
- [ ] **Phase 0** — Move `settings.json` to Postgres (SQLAlchemy 2.0 + Alembic). *Fixes a live
      bug: the file does not survive an ephemeral filesystem, so per-guild settings are lost on
      every cloud deploy.*
- [ ] **Phase 1** — React + Vite + TypeScript + Tailwind v4 + shadcn frontend with an
      iOS-style design system (materials, stacked shadows, spring physics, widget grid).
- [ ] **Phase 2** — Versioned JSON API (`/api/v1/*`), migrate the website to Open-Meteo,
      ship the public weather PWA with a ⌘K palette over all 64 commands.
- [x] **Phase 3** — Discord OAuth2 login, Redis sessions, per-guild settings dashboard.
      *Ships sign-in, the guild picker and a **read-only** guild overview. Editing settings
      (`PATCH /guilds/:id/settings`), `audit_log` and Fernet token storage are deliberately
      deferred — see [`WEB_DASHBOARD_PLAN.md`](WEB_DASHBOARD_PLAN.md) §8.*
- [ ] **Phase 4** — Redis bot↔web bridge; persistent playlists, Spotify import, autoplay,
      now-playing buttons, and a web music remote.
- [ ] **Phase 5** — Weather subscriptions: daily digests, severe-weather watcher, and
      class-suspension auto-announce; `/setlocation` per-user default city.
- [ ] **Phase 6** — `/summarize`, per-channel conversation memory, per-guild personas,
      `/translate`.
- [ ] **Phase 7** — Accessibility, performance, rate limiting, audit log, tests,
      deployment cleanup.

**Backlog (not scheduled)**

- [ ] Support more music sources (SoundCloud, Bandcamp).
- [ ] Admin/moderation utilities: automod, reaction roles, starboard, leveling + web
      leaderboard, reminders, polls, welcome cards, giveaways.
- [ ] Sharding readiness and metrics.
- [ ] i18n.

---

## 13. Changelog

### 1.4 — Phase 3 dashboard UI
- Sign-in screen, guild picker, and a read-only per-guild overview at `/login`, `/g`, `/g/:id`.
- `RequireAuth` distinguishes pending, 401, unconfigured, and network failure — a connection blip
  never presents as a sign-out.
- API client gains methods, CSRF headers and a typed `ApiError` carrying status and code, so 401
  and 403 can be told apart; 4xx responses are no longer retried.
- **Fixed a live PWA bug:** the service worker answered every same-origin navigation from the
  cached shell, including `/api/v1/auth/login` and Discord's callback, so signing in would have
  silently done nothing once the worker activated.
- `ListRow` and `PressableButton` gained optional props for navigable rows; `Skeleton` added and
  `CapsuleToast` given an error tone, establishing the loading and error conventions.

### 1.3 — Phase 3 auth backend
- Discord OAuth2 authorization-code flow at `/api/v1/auth/{login,callback,logout}`, with the
  `state` bound to both Redis and an `HttpOnly` cookie so login-CSRF is closed.
- Redis-backed server-side sessions: opaque ids, `HttpOnly` + `SameSite=Lax` cookies, a sliding
  TTL renewed with a single `GETEX`, and a hard `created_at` cap. No `SECRET_KEY` required.
- Synchronizer-token CSRF enforced across the whole `/api/v1` blueprint, plus `no-store` and
  `Vary: Cookie` on authenticated responses.
- `GET /api/v1/me` and a read-only `GET /api/v1/guilds/<id>`.
- New `web_users` and `guilds` tables, a lazy engine that keeps Flask away from the storage
  singleton, and an Alembic baseline covering all four tables.
- The bot publishes a `zephyr:guilds` membership snapshot so the dashboard can tell which servers
  it is actually in.
- `REDIS_URL` and the OAuth secrets wired into the Render web service, which previously had none.
- Deliberate deferrals and departures from the plan are recorded in
  [`WEB_DASHBOARD_PLAN.md`](WEB_DASHBOARD_PLAN.md) §8.

### 1.2 — Web dashboard planning *(docs only; no code changes)*
- Added [`WEB_DASHBOARD_PLAN.md`](WEB_DASHBOARD_PLAN.md): architecture, data model, API
  surface, iOS design-token spec, and a 7-phase delivery plan.
- Chose TypeScript, Vite, Tailwind v4, shadcn/ui, Motion, TanStack Query.
- Chose Postgres over `settings.json`, and Redis pub/sub as the bot↔web bridge.
- Rewrote the roadmap (§12) around the phased build.

### 1.1 — Cloud-ready deployment
- Added cross-platform FFmpeg/Opus detection for Linux, macOS, and Windows.
- Added portable `settings.json` / Redis storage abstraction (`zephyr/services/storage.py`).
- Added production WSGI entry point (`wsgi.py`) and `/health` endpoint.
- Added deployment artifacts: `Dockerfile`, `docker-compose.yml`, `Procfile`, `render.yaml`, `vercel.json`, `vercel_handler.py`, `aws_lambda_handler.py`, `.dockerignore`.
- Updated documentation with local, Docker, Render, Vercel, and AWS deployment guides.

### 1.0 — Baseline
- Centralized help system with categorized slash help commands.
- Music feature supports YouTube URLs/playlists and Spotify links resolved to YouTube.
- Weather `/forecast` and `/class` migrated to Open-Meteo with OpenWeatherMap fallback.
- 64 slash commands and 13 prefix commands available.

---

## Appendix: Counts at a Glance

- **Slash commands:** 64 (including aliases)
- **Prefix commands:** 13
- **Cogs:** 5
- **Entry points:** 2 (`run_bot.py`, `run_web.py`)
- **External data providers:** 3 (Open-Meteo, OpenWeatherMap, Google Gemini)
