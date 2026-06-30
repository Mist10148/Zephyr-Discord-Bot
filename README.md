# Zephyr Discord Bot

A modular, multi-purpose Discord bot that bundles a **weather service**, a full-featured
**music player**, a **Google Gemini AI chat** companion, and **text-to-speech** — plus a
companion **Flask weather website**. Built with `discord.py` and organized into clean,
self-contained cogs.

---

## ✨ Overview

Zephyr started life as a single 3,000-line script and was rebuilt into a maintainable package:

- **Weather** — current conditions, forecasts, air quality, typhoon alerts, and a heat-index
  "class suspension" predictor (powered by OpenWeatherMap). Every weather command works as both
  a slash command and a classic prefix command.
- **Music** — a Groovy-style player streaming from **YouTube** and **Spotify**, with a queue,
  search-and-pick, seeking, loop modes, live audio effects (nightcore, vaporwave, 8D, reverb,
  bass boost, pitch…), and on-demand lyrics.
- **AI chat** — talk to the bot by mentioning it, replying to it, or DMing it. Backed by Google
  Gemini with a customizable persona, per-server/DM preferences, response-format options, local
  rate-limit tracking, and image generation.
- **Text-to-speech** — make the bot speak in a voice channel in your chosen language.
- **Website** — a small Flask app that shows Iloilo City's forecast and lets you search any city.

> The bot exposes **64 slash commands** and **13 prefix commands** across these features.

---

## 🧰 Tech stack

| Area | Technology |
|------|------------|
| Language | **Python 3.13** |
| Discord | [`discord.py`](https://discordpy.readthedocs.io/) (with voice / PyNaCl) |
| AI | [`google-genai`](https://ai.google.dev/) (Gemini) |
| Music sources | [`yt-dlp`](https://github.com/yt-dlp/yt-dlp), [`spotipy`](https://spotipy.readthedocs.io/) (Spotify Web API) |
| Audio | **FFmpeg**, **Opus**, [`gTTS`](https://gtts.readthedocs.io/) |
| Weather data | [OpenWeatherMap API](https://openweathermap.org/api) |
| Website | **Flask**, `geopy` (Nominatim), `timezonefinder`, `pytz`, [Swiper.js](https://swiperjs.com/) |
| Config | [`python-dotenv`](https://pypi.org/project/python-dotenv/) |
| Cloud | **Docker**, **Gunicorn**, optional **Redis** |

---

## 📂 Project structure

```
Zephyr-Discord-Bot/
├── run_bot.py                # start the Discord bot
├── run_web.py                # start the Flask website (local dev)
├── wsgi.py                   # production WSGI entry point
├── aws_lambda_handler.py     # AWS Lambda entry point (website)
├── vercel_handler.py         # Vercel serverless entry point (website)
├── Dockerfile                # container image for bot or website
├── docker-compose.yml        # local orchestration: Redis + bot + website
├── Procfile                  # Render/Heroku process definitions
├── render.yaml               # Render Blueprint
├── vercel.json               # Vercel routing config
├── requirements.txt
├── .env.example              # template for your secrets
├── settings.json             # persisted per-context AI settings (auto-managed)
├── libopus-0.x64.dll         # Opus codec (Windows voice)
├── ffmpeg/                   # FFmpeg binaries (not committed — see Requirements)
├── zephyr/                   # bot package
│   ├── config.py             # loads .env + constants
│   ├── client.py             # bot instance, cog loading, events (on_message, on_ready…)
│   ├── core/                 # opus loader + ffmpeg resolver
│   ├── utils/                # weather/time helpers + pagination
│   ├── services/             # AI engine + portable storage
│   │   ├── gemini.py
│   │   └── storage.py
│   └── cogs/                 # weather, music, chat, voice_tts, help
└── website/
    ├── app.py                # Flask weather app
    └── templates/index.html
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
| `/use` | Link to the web app |
| `/helpweather` | Weather command help |
| `/ping` | Bot latency |

### 🎵 Music
**Playback:** `/play` · `/playskip` · `/playnext` · `/msearch` · `/now` (`/np`) · `/pause` · `/resume` · `/stop` · `/seek` · `/forward` · `/rewind`
**Queue:** `/queue` · `/skip` · `/jump` · `/move` · `/remove` · `/clear` · `/shuffle` · `/loop` · `/loopqueue`
**Voice:** `/join` · `/summon` · `/leave` · `/disconnect`
**Audio & effects:** `/volume` · `/bassboost` (`/bass_boost`) · `/pitch` · `/nightcore` · `/vaporwave` · `/slowed` · `/reverb` · `/slownrev` · `/16d` · `/reset_effects` · `/247`
**Extras:** `/lyrics [query]` · `/helpmusic`

> Supports YouTube links/search, Spotify tracks, playlists, and albums (resolved to YouTube audio).

### 💬 AI Chat & TTS
| Command | Description |
|---------|-------------|
| `/prompt <message> [attachment]` | Ask the AI (supports image & `.txt` attachments) |
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

> **Important:** the Discord bot needs a persistent process and cannot run on serverless platforms
> such as Vercel or AWS Lambda. The Flask website can.

### Quick reference

| Platform | Bot | Website | Notes |
|----------|-----|---------|-------|
| **Docker / Docker Compose** | ✅ | ✅ | `Dockerfile` + `docker-compose.yml` included. |
| **Render** | ✅ | ✅ | Use the included `render.yaml` Blueprint. |
| **Heroku** | ✅ | ✅ | Use the included `Procfile`. |
| **Vercel** | ❌ | ✅ | Use `vercel.json` + `vercel_handler.py`. |
| **AWS Lambda** | ❌ | ✅ | Use `aws_lambda_handler.py`. |
| **AWS EC2 / ECS / Fargate** | ✅ | ✅ | Use the `Dockerfile`. |

### Render one-click deploy

1. Push this repo to GitHub.
2. In Render, click **New +** → **Blueprint** and connect the repo.
3. Fill in the environment variables in the Render dashboard.

Render will create the website, the bot worker, and a Redis instance automatically.

> **Note:** Render background workers require a paid plan. The web service can use Render's free tier, but it will spin down after inactivity.

---

## ⚙️ Configuration (`.env`)

| Variable | Required | Description |
|----------|:--------:|-------------|
| `DISCORD_TOKEN` | ✅ | Discord bot token |
| `OPENWEATHER_API_KEY` | ✅ | OpenWeatherMap key (used by the bot *and* website) |
| `GEMINI_API_KEY` | ✅ | Google Gemini API key |
| `SPOTIFY_CLIENT_ID` | ✅ | Spotify app client ID |
| `SPOTIFY_CLIENT_SECRET` | ✅ | Spotify app client secret |
| `FFMPEG_PATH` | — | Explicit path to FFmpeg (otherwise auto-detected) |
| `WEB_APP_URL` | — | URL shown by `/use` (defaults to the project's web app) |
| `FLASK_HOST` / `FLASK_PORT` | — | Website host/port (default `0.0.0.0:5000`) |
| `PORT` | — | Cloud-platform port override (overrides `FLASK_PORT`) |
| `REDIS_URL` | — | Optional Redis connection for shared AI settings |
| `SETTINGS_PATH` | — | Custom path for `settings.json` |
| `FLASK_DEBUG` | — | Set to `1` to enable Flask debug mode |

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
