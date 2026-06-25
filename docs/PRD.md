# Zephyr — Product Requirements Document (PRD)

**Version:** 1.0  
**Last updated:** 2026-06-25  
**Owner:** Zephyr Discord Bot project  

---

## 1. Overview

Zephyr is a modular, multi-purpose Discord bot built for community servers. It bundles a weather service, a Groovy-style music player, a Google Gemini AI chat companion, text-to-speech, and a small Flask companion website — all organized into clean, self-contained cogs.

The bot is written in Python 3.13 using `discord.py`, exposes **64 slash commands** (including aliases) and **13 prefix commands**, and runs on Windows with bundled FFmpeg/Opus binaries.

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
| Audio codec | FFmpeg, Opus (`libopus-0.x64.dll`) |
| Weather data | Open-Meteo (primary), OpenWeatherMap (fallback) |
| Website | Flask, `geopy`, `timezonefinder`, `pytz`, Swiper.js |
| Configuration | `python-dotenv` (`.env`) + `settings.json` |
| Utilities | `aiohttp`, `requests`, `async-timeout` |

---

## 5. Architecture

```
project-root/
├── run_bot.py              # Discord bot entry point
├── run_web.py              # Flask website entry point
├── requirements.txt
├── .env.example            # Secret/template file
├── settings.json           # Persisted per-context AI settings
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
│   ├── services/           # AI engine
│   │   └── gemini.py
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
- **Bot:** `python run_bot.py` → validates config → runs `ZephyrBot`.
- **Website:** `python run_web.py` → validates web config → runs Flask on `FLASK_HOST:FLASK_PORT`.

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
| `FFMPEG_PATH` | Optional | Path to `ffmpeg.exe` |
| `WEB_APP_URL` | Optional | URL shown by `/use` |
| `FLASK_HOST` / `FLASK_PORT` | Optional | Website bind address |

`settings.json` stores per-server/DM AI preferences and chat context; it is auto-managed and git-ignored.

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

- [ ] Add persistent music playlists.
- [ ] Support more music sources (SoundCloud, Bandcamp).
- [ ] Add server-specific settings for default AI model and music volume.
- [ ] Migrate the website to Open-Meteo for consistency with the bot.
- [ ] Add admin/moderation utilities (optional cog).
- [ ] Add unit/integration tests for cogs and helpers.

---

## 13. Changelog

### 1.0 — Current
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
