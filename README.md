# Zephyr Discord Bot

A modular, multi-purpose Discord bot that bundles a **weather service**, a full-featured
**music player**, a **Google Gemini AI chat** companion, and **text-to-speech** — plus a
companion **Flask weather website**. Built with `discord.py` and organized into clean,
self-contained cogs.

---

## ✨ Overview

Zephyr started life as a single 3,000-line script and was rebuilt into a maintainable package:

- **Weather** — current conditions, forecasts, air quality, typhoon alerts, and a heat-index
  "class suspension" predictor. Every weather command works as both a slash command and a classic
  prefix command, and `/setlocation` gives each person their own default city.
- **Weather subscriptions** — daily digests on a schedule you choose, plus severe-weather and
  class-suspension watches that stay quiet until there is something worth saying.
- **Music** — a Groovy-style player streaming from **YouTube** and **Spotify**, with a queue,
  search-and-pick, seeking, loop modes, live audio effects (nightcore, vaporwave, 16D, reverb,
  bass boost, pitch…), on-demand lyrics, saved playlists, autoplay, and now-playing buttons.
- **AI chat** — talk to the bot by mentioning it, replying to it, or DMing it. Backed by Google
  Gemini with a customizable persona, per-server/DM preferences, response-format options, local
  rate-limit tracking, and image generation.
- **Text-to-speech** — make the bot speak in a voice channel in your chosen language.
- **Dashboard** — a React PWA: a public weather page for anyone, and for server admins a live
  music remote, weather subscriptions and editable settings, signed in with Discord.

> The bot exposes **75 slash commands** and **13 prefix commands** across these features.

---

## 🧰 Tech stack

| Area | Technology |
|------|------------|
| Language | **Python 3.13** |
| Discord | [`discord.py`](https://discordpy.readthedocs.io/) (with voice / PyNaCl) |
| AI | [`google-genai`](https://ai.google.dev/) (Gemini) |
| Music sources | [`yt-dlp`](https://github.com/yt-dlp/yt-dlp), [`spotipy`](https://spotipy.readthedocs.io/) (Spotify Web API) |
| Audio | **FFmpeg**, **Opus**, [`gTTS`](https://gtts.readthedocs.io/) |
| Weather data | [Open-Meteo](https://open-meteo.com/) (primary), [OpenWeatherMap](https://openweathermap.org/api) (fallback) |
| Website | **Flask** + **React 19**, TypeScript, Vite, Tailwind v4, TanStack Query, Motion, dnd-kit |
| Persistence | **SQLAlchemy 2.0** + **Alembic** (SQLite by default, Postgres in production) |
| Bot ↔ web | **Redis** snapshots and pub/sub |
| Config | [`python-dotenv`](https://pypi.org/project/python-dotenv/) |
| Cloud | **Docker**, **Gunicorn**, **Redis** |

---

## 📂 Project structure

```
Zephyr-Discord-Bot/
├── run_bot.py                # start the Discord bot
├── run_web.py                # start the Flask website (local dev)
├── wsgi.py                   # production WSGI entry point
├── Dockerfile                # container image for bot or website
├── docker-compose.yml        # local orchestration: Postgres + Redis + bot + website
├── Procfile                  # Render/Heroku process definitions
├── render.yaml               # Render Blueprint
├── alembic.ini               # migration config (see docs/DEPLOYMENT.md)
├── requirements.txt
├── .env.example              # template for your secrets
├── settings.json             # legacy per-context AI settings (superseded by the database)
├── libopus-0.x64.dll         # Opus codec (Windows voice)
├── ffmpeg/                   # FFmpeg binaries (not committed — see Requirements)
├── scripts/                  # one-off tools (settings import, icon generation)
├── zephyr/                   # bot package
│   ├── config.py             # loads .env + constants
│   ├── client.py             # bot instance, cog loading, events (on_message, on_ready…)
│   ├── core/                 # opus loader + ffmpeg resolver
│   ├── db/                   # models, Core repositories, engine, Alembic migrations
│   ├── utils/                # weather + alert evaluation, time helpers, pagination
│   ├── services/             # AI engine, storage, Redis client, Spotify, bot↔web bridge
│   └── cogs/                 # weather, weather_alerts, music, chat, voice_tts, help
└── website/                  # Flask API + React SPA
    ├── __init__.py           # create_app factory
    ├── api/                  # /api/v1 (auth, me, guilds, player, playlists, weather, weather_subs)
    ├── security.py           # CSP + security headers
    ├── session.py            # Redis-backed sessions
    ├── spa.py                # serves the built SPA
    ├── static/               # Vite build output (gitignored)
    └── frontend/             # React + TypeScript + Vite source
```

---

## 🤖 Commands

### 🌦️ Weather  *(available as both `/slash` and prefix commands)*
| Command | Description |
|---------|-------------|
| `/weather <city>` | Current weather, air quality & precipitation |
| `/forecast <city>` | Paginated 3-day forecast |
| `/temperature <city>` | Current temperature |
| `/description <city>` | Weather description |
| `/humidity <city>` | Humidity |
| `/pressure <city>` | Atmospheric pressure |
| `/windspeed <city>` | Wind speed |
| `/air <city>` | Air quality index & pollutants |
| `/precipitation <city>` | Rain/snow details |
| `/typhoon` | Latest typhoon alert for Iloilo City |
| `/class` | Class-suspension forecast based on heat index |
| `/search <city>` | Quick weather lookup |
| `/setlocation [city]` | Set your default city (leave empty to clear it) |
| `/mylocation` | Show your default city |
| `/weather-subscribe <kind> <location>` | Post weather to a channel on a schedule or a watch |
| `/weather-subs` | List this server's subscriptions |
| `/weather-unsubscribe <id>` | Remove one |
| `/weather-preview <id>` | See what a subscription would post right now |
| `/use` | Link to the web app |
| `/helpweather` | Weather command help |
| `/ping` | Bot latency |

### 🎵 Music
**Playback:** `/play` · `/playskip` · `/playnext` · `/msearch` · `/now` (`/np`) · `/pause` · `/resume` · `/stop` · `/seek` · `/forward` · `/rewind`
**Queue:** `/queue` · `/skip` · `/jump` · `/move` · `/remove` · `/clear` · `/shuffle` · `/loop` · `/loopqueue` · `/autoplay`
**Playlists:** `/save` · `/load` · `/playlists` · `/playlist-delete`
**Voice:** `/join` · `/summon` · `/leave` · `/disconnect`
**Audio & effects:** `/volume` · `/bassboost` (`/bass_boost`) · `/pitch` · `/nightcore` · `/vaporwave` · `/slowed` · `/reverb` · `/slownrev` · `/16d` · `/reset_effects` · `/247`
**Extras:** `/lyrics [query]` · `/helpmusic`

> Supports YouTube links/search, Spotify tracks, playlists, and albums (resolved to YouTube audio).
> Saved playlists store titles and links, and re-find a track by title if its link stops working.

### 💬 AI Chat & TTS
| Command | Description |
|---------|-------------|
| `/prompt <message> [attachment]` | Ask the AI (supports image & `.txt` attachments) |
| `/forget` | Reset the AI's memory of this channel (needs **Manage Messages**) |
| `/settings` | Choose the Gemini model & response format |
| `/output` | Quickly toggle embed vs. plain-text replies |
| `/token` | Show this session's Gemini usage / rate-limit status |
| `/image-gen <prompt>` | Generate an image with Gemini |
| `/generate <prompt>` | Optional image-generator hook (stub by default) |
| `/say <text>` | Speak text in your voice channel |
| `/language <code>` | Set the TTS language (e.g., `en`, `ja`) |
| `/helpchat` | Chat command help |

You can also just **@mention**, **reply to**, or **DM** the bot to chat with it directly.

### 📖 Help
`/help` — a paginated overview of every command, grouped by category.

---

## 📋 Requirements

- **Python 3.13+**
- **FFmpeg** — required for all voice/music/TTS audio. The bot looks for it in this order:
  1. the `FFMPEG_PATH` value in your `.env`,
  2. a bundled `ffmpeg/` folder next to the project,
  3. FFmpeg on your system `PATH`.

  The binaries aren't committed to this repo (they exceed GitHub's file-size limit), so either
  [install FFmpeg](https://ffmpeg.org/download.html) and add it to your `PATH`, drop
  `ffmpeg.exe`/`ffplay.exe`/`ffprobe.exe` into an `ffmpeg/` folder, or point `FFMPEG_PATH` at it.
- **Opus** — bundled as `libopus-0.x64.dll` on Windows; installed by the Dockerfile on Linux.
- **API keys / tokens** (all free to obtain):
  - A **Discord bot token** — [Discord Developer Portal](https://discord.com/developers/applications)
    (enable the *Message Content*, *Server Members*, and *Presence* privileged intents).
  - An **OpenWeatherMap API key** — https://openweathermap.org/api
  - A **Google Gemini API key** — https://aistudio.google.com/app/apikey
  - **Spotify** client ID & secret — https://developer.spotify.com/dashboard

---

## 🚀 Setup & running

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

# 4. Configure your secrets
copy .env.example .env          # Windows  (cp on macOS/Linux)
#   then edit .env and fill in your tokens/keys
```

### Run the bot
```bash
python run_bot.py
```
On startup it loads every cog, syncs the slash commands with Discord, and sets its presence to
`Listening to /help`.

### Run the website
```bash
python run_web.py
```
Then open **http://localhost:5000** — the home panel shows Iloilo City's forecast, and a second
panel lets you search the weather for any city.

---

## ☁️ Deploy to the cloud

Zephyr can run locally **or** in the cloud. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the full guide.

> **Important:** neither half of Zephyr runs on serverless. The bot needs a persistent process for
> its gateway connection, and the website needs a persistent Redis connection for dashboard
> sessions. Serverless support (`vercel.json`, `vercel_handler.py`, `aws_lambda_handler.py`) was
> removed accordingly.

### Quick reference

| Platform | Bot | Website | Notes |
|----------|-----|---------|-------|
| **Docker / Docker Compose** | ✅ | ✅ | `Dockerfile` + `docker-compose.yml`, with Postgres and Redis included. |
| **Render** | ✅ | ✅ | Use the included `render.yaml` Blueprint. |
| **Heroku** | ✅ | ✅ | Use the included `Procfile`. |
| **AWS EC2 / ECS / Fargate** | ✅ | ✅ | Use the `Dockerfile`. |
| **Vercel / AWS Lambda** | ❌ | ❌ | Serverless cannot hold the gateway or session connections. |

### Render one-click deploy

1. Push this repo to GitHub.
2. In Render, click **New +** → **Blueprint** and connect the repo.
3. Fill in the environment variables in the Render dashboard.

Render will create the website, the bot worker, a Postgres database, and a Redis instance
automatically. For the dashboard, also set `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET` on the
**web** service — see [Discord OAuth setup](docs/DEPLOYMENT.md#discord-oauth-setup).

> **Note:** Render background workers require a paid plan. The web service can use Render's free tier, but it will spin down after inactivity.

### Before going live

- **Set `WEB_APP_URL`** to your deployed website, or `/use` will report that it is unconfigured.
- **Run migrations** on an existing database: `alembic upgrade head` (a fresh one is created
  automatically). See [Database migrations](docs/DEPLOYMENT.md#database-migrations).
- **Serve over HTTPS.** Session cookies are marked `Secure` automatically for `https://` origins,
  and HSTS is only sent from one.
- The website sends a Content-Security-Policy that allows images from `cdn.discordapp.com`. If you
  add another image host, update [`website/security.py`](website/security.py) or those images will
  be blocked.

---

## 🗺️ Web dashboard

> **Status: phases 0–6 shipped.** Sign-in, the servers you administer, editable settings, a live
> music remote, weather subscriptions, and AI personas/memory all work today. Still to come:
> hardening (phase 7).

- **Public** — a weather PWA with a ⌘K palette over every command, installable to a home screen.
- **Auth** — Discord OAuth2; you see and manage only the servers you already administer.
- **Music remote** — the live queue and full transport control from the browser, bridged to the bot
  over Redis. The bot re-validates every permission against its own Discord cache before acting, so
  what the page lets you click is only ever a suggestion.
- **Playlists** — save a queue from Discord, then reorder it in the browser (with the keyboard, if
  you prefer), or import one from Spotify.
- **Weather subscriptions** — daily digests, severe-weather watches and class-suspension
  advisories, with a preview that runs the same code the scheduler does.
- **Settings** — prefix, locale, timezone, default volume, DJ role and music channels, with
  channel and role pickers answered by the bot in real time.
- **Postgres** replaces `settings.json`, so per-guild settings survive cloud deploys.

Full architecture, data model, API surface, the 7-phase delivery plan and a running log of every
deliberate departure from it: [`docs/WEB_DASHBOARD_PLAN.md`](docs/WEB_DASHBOARD_PLAN.md).

---

## ⚙️ Configuration (`.env`)

| Variable | Required | Description |
|----------|:--------:|-------------|
| `DISCORD_TOKEN` | ✅ | Discord bot token |
| `OPENWEATHER_API_KEY` | ✅ | OpenWeatherMap key (used by the bot *and* website) |
| `GEMINI_API_KEY` | ✅ | Google Gemini API key |
| `SPOTIFY_CLIENT_ID` | ✅ | Spotify app client ID. Also set it on the website for the dashboard's playlist import |
| `SPOTIFY_CLIENT_SECRET` | ✅ | Spotify app client secret |
| `FFMPEG_PATH` | — | Explicit path to FFmpeg (otherwise auto-detected) |
| `WEB_APP_URL` | — | URL shown by `/use`. **Not** the OAuth origin — see `WEB_PUBLIC_URL` |
| `FLASK_HOST` / `FLASK_PORT` | — | Website host/port (default `0.0.0.0:5000`) |
| `PORT` | — | Cloud-platform port override (overrides `FLASK_PORT`) |
| `REDIS_URL` | — | Shared AI settings, **required for dashboard sessions**, and the bot↔web bridge the music remote runs on |
| `SETTINGS_PATH` | — | Custom path for `settings.json` |
| `FLASK_DEBUG` | — | Set to `1` to enable Flask debug mode |
| `DATABASE_URL` | — | Postgres/SQLite URL (defaults to `data/zephyr.db`) |

### Dashboard (Discord OAuth)

Leave all of these unset to serve only the public weather site. Setting some but not all
of `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` and `REDIS_URL` fails fast at startup.

| Variable | Required | Description |
|----------|:--------:|-------------|
| `DISCORD_CLIENT_ID` | dashboard | OAuth2 client ID (same Discord application as the bot) |
| `DISCORD_CLIENT_SECRET` | dashboard | OAuth2 client secret |
| `REDIS_URL` | dashboard | Sessions are server-side and shared across gunicorn workers |
| `WEB_PUBLIC_URL` | — | Public origin of the dashboard. Render supplies `RENDER_EXTERNAL_URL` |
| `DISCORD_REDIRECT_URI` | — | Defaults to `<WEB_PUBLIC_URL>/api/v1/auth/callback` |
| `SESSION_TTL_SECONDS` | — | Sliding session lifetime (default 7 days) |
| `SESSION_MAX_AGE_SECONDS` | — | Hard cap no activity extends (default 30 days) |
| `AUTH_COOKIE_SECURE` | — | Auto-on for `https://` origins |
| `GUILDS_FRESH_SECONDS` | — | How long a session's guild list is trusted (default 1 hour) |
| `TRUST_PROXY_HEADERS` | — | Trust `X-Forwarded-*`. Auto-on under Render |

`.env` is git-ignored and never committed. `settings.json` (per-context AI preferences) is also
kept local unless you set `REDIS_URL`.

---

## 📝 Notes

- AI responses, the persona, model fallbacks, and all rate-limit logic are configurable in
  `zephyr/services/gemini.py`.
- `/generate` is an optional hook that looks for a separate `image_generator` module; if it's not
  present it simply reports that image generation is unavailable. The built-in Gemini image
  command is `/image-gen`.
- Privileged intents must be enabled in the Discord Developer Portal for chat and some features to
  work.
- When running multiple bot instances in the cloud, set `REDIS_URL` so AI settings stay in sync.

---

## 📄 License

This project is provided as-is for personal use. Add a license of your choice if you intend to
share or distribute it.
