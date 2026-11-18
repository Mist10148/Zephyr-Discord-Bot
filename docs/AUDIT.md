# Bug audit — November 2026

A read-through of the docs against the code, with the music feature first. Every item below was
traced in the code; the ones marked **fixed** have a regression test. The AI feature
(`zephyr/cogs/chat.py`, `zephyr/services/gemini.py`, `website/api/ai.py`) was out of scope.

## Music

| # | Problem | Effect | Status |
|---|---------|--------|--------|
| M1 | A finished track stayed in `VoiceState.current`, so an idle player looked busy | A seek or effect toggle while idle (or while the next track loaded) restarted the dead track, left the `next` signal set, and the player then dropped **every** following track as "could not stream" | fixed |
| M2 | `/skip`, `/stop`, `/jump` while a track was loading | Reported success, then the track played anyway; the leftover flag later aborted a `/seek` | fixed |
| M3 | Stale `current` elsewhere | `/now` and `/queue` showed a finished song; `/join` refused non-DJs; TTS `/say` refused after any song | fixed |
| M4 | yt-dlp flat results are lazy generators, iterated on the event loop | Every text search, `/msearch`, title re-resolve and autoplay refill blocked all guilds and the gateway heartbeat, outside any timeout; huge playlists were read to the end before being cut to 200 | fixed |
| M5 | Timed-out yt-dlp lookups kept running on the shared default executor | Could starve database, Redis and TTS work | fixed (own 4-thread pool) |
| M6 | `/queue` put ~10 rows of links, durations and mentions in one 1,024-char field | `/queue` failed ("Something went wrong") whenever a page held ~6+ YouTube tracks | fixed |
| M7 | Titles with `[` `]` broke queue links; Uploader showed `[name](None)` | Broken markdown | fixed |
| M8 | `/msearch` menu never disabled; the same `Track` could be queued twice | Failed retry of one copy deleted the other | fixed |
| M9 | `/playskip` and `/playnext` skipped the DJ check | Anyone could skip past a DJ-only rule | fixed |
| M10 | Autoplay and 24/7 read only before the idle wait | Turning autoplay on while idle played nothing; turning 24/7 on did not stop the 3-minute disconnect | fixed |
| M11 | Queue loop toggled mid-track | Playing song left out of the rotation, or played once more at the end | fixed |
| M12 | `is_spotify_url` was a substring check | `https://10.0.0.5/?spotify.link` was fetched with redirects — a blind request to internal addresses | fixed |
| M13 | `/save … public:True` over an existing playlist kept it private; names saved case-sensitively but loaded case-insensitively | `/playlist-delete chill` could delete "Chill" | fixed |
| M14 | Nothing released a guild's player when the bot left it | A 24/7 player ran for the life of the process | fixed |
| M15 | Now-playing buttons waited for the Redis write before answering | "This interaction failed" after the action had happened | fixed |
| M16 | `player.state` for a guild the bot is not in | `AttributeError` text sent to the dashboard | fixed |
| M17 | `/queue`, `/lyrics` in another channel moved where now-playing posts go | Messages hopped channels | fixed |
| — | A paused player never idles out | Stays connected while paused | known, left as is |
| — | No auto-leave from an empty channel; 24/7 not persisted across restarts; dashboard progress may lag one poll; YouTube may challenge cloud IPs | | known, left as is (ENHANCEMENTS.md) |

## Weather

| # | Problem | Effect | Status |
|---|---------|--------|--------|
| W1 | `/forecast` and `/class` showed `str(e)` from `raise_for_status()` | **The OpenWeatherMap API key was shown to users** in the error | fixed |
| W2 | OpenWeatherMap called over `http://` | Key sent unencrypted | fixed |
| W3 | City interpolated raw into `q=` | `&`, `#`, `?` broke or injected query parameters | fixed |
| W4 | `/typhoon` (prefix) said "no alerts" when One Call failed | A key without the One Call 3.0 subscription always reported "no alerts", even during a typhoon | fixed |
| W5 | `/class` and the class-suspension subscription used different heat scales | Contradicting advice at 15 °C | fixed |
| W6 | Fallback forecast: `max()` of empty slots, first-slot condition, unrounded km/h | Crash, unrepresentative day, `12.9599999 km/h` | fixed |
| W7 | Every city's time printed in PHT; prefix forecast in UTC paired with the wrong hour's AQI | Wrong times and air quality | fixed (redesign) |
| W8 | `pop` read from current conditions (never present); pollutants missing shown as `0`; hard `['wind']['speed']` indexing | Dead field, misleading zeros, crashes on partial data | fixed (redesign) |
| W9 | `/typhoon` bulletin could exceed 1,024 chars; missing start/end became 1970 | Discord rejected the embed | fixed (redesign) |
| W10 | Weather-alerts cog had no error handler | Non-managers got "did not respond"; errors after defer hung | fixed |
| W11 | `/weather-subscribe` defaulted to UTC | An 08:00 Iloilo digest posted at 16:00 Manila | fixed |
| W12 | Digest marked run before delivery | A provider hiccup lost that day's digest | fixed |
| W13 | Digest created after today's time posted immediately | | fixed |
| W14 | `/weather-subs` silently cut at 25 | | fixed |
| W15 | Web `/weather`: missing daily key → 500; `days=0` → 502; subscription preview → HTML 500 | | fixed |
| — | `/class` and `/typhoon` are Iloilo-only | By design; now labelled in /help | documented |

## Help

| # | Problem | Status |
|---|---------|--------|
| H1 | Five music pages before weather; `/disconnect` in two places | reorganized: Weather → Music → Voice → AI → Utility → Help |
| H2 | Argument hints did not match the real parameters (`/play <query>` vs `search`, `/seek <time>` vs `position`, `/move <from> <to>`, …) | fixed; a test now checks every hint |
| H3 | Prefix `helpweather` and the welcome message were stale hand-written lists | now rendered from the registry |
| H4 | Help pager overwrote footers, let anyone turn pages, never disabled on timeout | fixed |

## Voice

| # | Problem | Status |
|---|---------|--------|
| V1 | `/language` was global to the bot and unvalidated | fixed: per server, checked against gTTS |

## Deployment & configuration

| # | Problem | Status |
|---|---------|--------|
| D1 | `alembic upgrade head` failed on any database `create_all()` built | fixed: fresh databases are stamped at head |
| D2 | `%` in `DATABASE_URL` broke Alembic | fixed |
| D3 | Compose published Redis (no password) on the host — forged bridge commands as any admin, forged sessions | fixed |
| D4 | `data/` (AI conversations, dashboard users) copied into Docker images | fixed |
| D5 | One sync gunicorn worker outside Render — one slow player action froze the site | fixed: 2 workers × 4 threads everywhere |
| D6 | `.env.example` set `FLASK_HOST=0.0.0.0`, defeating the debug-mode localhost safeguard | fixed |
| D7 | Compose bot lacked `WEB_PUBLIC_URL`, so `/use` linked to 127.0.0.1; Compose redirect URI undocumented | fixed |
| D8 | Render worker created tables before the website's migration ran | fixed: `DB_AUTO_CREATE=0` on the worker |
| D9 | Silent fallback to `settings.json` when the database is down at boot | now warns loudly; documented |
| D10 | Docs: Presence intent "optional" (it is required); local web steps missing the SPA build; "several bot instances" encouraged | docs fixed |

## Found and intentionally not changed

- **AI (out of scope):** in a DM, a text command such as `/weather Manila` also gets an AI reply
  (`client.py` `on_message`).
- **Bridge reader holds a default-executor thread** permanently; with yt-dlp now on its own pool
  this is much less likely to starve, so it is left for now.
- **Shutdown ordering:** a presence heartbeat already in flight can land after "offline"; the
  dashboard may show the bot online for up to 30 s after shutdown.
- **Playlist web API:** delete/patch write no audit row; the Spotify client is rebuilt per import.
- **Public web endpoints** (`/weather`, `/geocode`) have no per-IP rate limit (ENHANCEMENTS 12.6).

---

# Second pass — November 2026

A second read of music and weather after the fixes above. Every item was traced in the code and
has a regression test.

## Music

| # | Problem | Effect | Status |
|---|---------|--------|--------|
| M18 | Loop=track kept `_last_track` after a replay failed to load | The dead track was retried forever, with a "Failed to load" post each time | fixed |
| M19 | `interaction_check` replied, then the error handler replied again | Two messages for a DM or for a channel outside the music channels | fixed |
| M20 | SQLite `lower()` only folds ASCII | `/save Été` twice failed; `/load` and `/playlist-delete` could not find non-ASCII names | fixed (`casefold()` in Python) |
| M21 | `/load` created the player with no announce channel | With no music channels set, no now-playing, buttons or errors were posted | fixed |
| M22 | Effects or `/seek` while a track loaded | Reported success but did nothing; the progress clock ran at the wrong speed | fixed: applied when the load finishes |
| M23 | A seek travelled through `_current_position`, outside the restart lock | A seek racing an effect restart was silently lost | fixed: explicit `position=` |
| M24 | Dashboard effects set toggles before validating pitch and bass | A bad value left effects half-applied | fixed |
| M25 | `/play` while `/say` was speaking | `voice.play` raised; the track could be dropped as "could not stream" | fixed: waits for the clip |
| M26 | `/msearch` pick | Said "Selected" after a refusal; a connect error went unanswered | fixed |
| M27 | ⏭️ button skipped at once for any listener | The documented skip vote was bypassed | fixed: same rule as `/skip` |
| M28 | Stream retries keyed by `id(track)` | Leaked, and a recycled id could drop a new track with no retry | fixed: flag on the Track |
| M29 | Dashboard rename only checked the exact-case unique constraint | Recreated "Chill"/"chill" duplicates | fixed |
| M30 | A song opened from a YouTube Mix (`list=RD…`) | Queued the whole 200-track Mix | fixed: only that video |

## Weather

| # | Problem | Effect | Status |
|---|---------|--------|--------|
| W16 | Imperial severe watches compared °F/mph against metric defaults | "Feels like 95°F" warnings daily; gales under 60 mph missed | fixed; old rows healed on read |
| W17 | The severe fingerprint had no date and was never cleared | The next day's identical storm or heat peak was never announced | fixed: cleared on a quiet tick |
| W18 | Dashboard edits skipped the "already past today" check | Re-enabling or rescheduling a digest posted it within a minute | fixed |
| W19 | `/setlocation` coordinates were never used | Commands re-geocoded the name and could land in another country | fixed |
| W20 | Threshold and `enabled` validation | `true` → 1 %, NaN broke the dashboard's JSON, `"false"` → on | fixed |
| W21 | "Cold Weather" counted as a class-suspension advisory | Every cool city got a daily watch post and a "heat index" Classes line | fixed (heat only; `/class` unchanged) |
| W22 | Changing a subscription's place kept the old time zone | An 08:00 digest moved to London posted at midnight there | fixed |
| W23 | Web forecast used today's UTC offset for all seven days | Times an hour off after a DST change | fixed |
| W24 | No Embed Links check; 4xx send errors retried every minute | A broken channel re-fetched the forecast all day | fixed |
| W25 | Raw database error posted publicly; duplicate subscriptions allowed | Leaked SQL; double posts | fixed |
