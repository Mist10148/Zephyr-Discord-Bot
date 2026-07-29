# Zephyr Web Dashboard — Design & Build Plan

**Status:** planning
**Created:** 2026-07-29
**Scope:** React/Vite/shadcn dashboard with Discord OAuth + four bot feature tracks

---

## 1. Decisions

| Question | Decision | Rationale |
|---|---|---|
| TS or JS | **TypeScript** | shadcn's CLI emits `.tsx`; the Flask JSON boundary is where bugs hide (current `POST /weather` returns `number \| "N/A"` unions that render as `NaN°`); Motion/dnd-kit/TanStack Query are all TS-first |
| Frontend location | `website/frontend/` → builds to `website/static/` | Flask serves the built SPA; keeps one deployable |
| API style | Versioned JSON under `/api/v1/*` | Current `POST /weather` takes **form data** — not consumable by a typed client |
| Weather provider (web) | **Migrate to Open-Meteo** | Keyless, one call gives hourly + daily + AQI + apparent temp, unifies bot/web behavior, removes the fragile `day_data[4]` "daytime" indexing |
| Database | **Postgres** + sync SQLAlchemy 2.0 | `settings.json` does not survive an ephemeral filesystem — see §7 |
| Bot ↔ web transport | **Redis** (pub/sub + snapshot keys) | `redis` is already a dependency; keeps the bot process authoritative |
| Live updates to browser | **Polling first** (TanStack `refetchInterval: 3000`), SSE later | SSE under gunicorn needs gevent workers; not worth the deploy complexity in v1 |
| Hosting | Flask web service + bot **worker** + Postgres + Redis | Vercel/Lambda cannot hold long-lived connections; see §7 |

---

## 2. Architecture

```
┌─────────────┐   HTTPS    ┌──────────────────────┐
│  Browser    │───────────▶│  Flask               │
│  React SPA  │◀───────────│  /api/v1/*  + SPA    │
└─────────────┘   JSON     └───────┬────────┬─────┘
                                   │        │
                            SQL    │        │  pub/sub + GET
                                   ▼        ▼
                          ┌────────────┐  ┌──────────┐
                          │  Postgres  │  │  Redis   │
                          └────────────┘  └──────────┘
                                   ▲        ▲
                            SQL    │        │  publish snapshots
                                   │        │  subscribe commands
                          ┌────────┴────────┴─────┐
                          │  Zephyr bot (worker)  │
                          │  discord.py, 5+ cogs  │
                          └───────────────────────┘
```

Two processes, one database. The bot is always the authority on Discord state and on
permissions — the web tier proposes, the bot decides.

### Bridge protocol

**Snapshots** (bot writes, web reads):
| Key | TTL | Payload |
|---|---|---|
| `zephyr:presence` | 30s | `{online, guild_count, latency_ms, uptime_s, shard}` — heartbeat |
| `zephyr:player:{guild_id}` | 60s | `{track, position_s, duration_s, paused, loop, volume, effects, queue[]}` |

Player snapshot is rewritten on every state change **and** every 5s while playing.

**Commands** (web publishes, bot executes):
```
PUBLISH zephyr:cmd  {id, guild_id, actor_id, action, args, issued_at}
PUBLISH zephyr:res:{id}  {ok, error?, data?}
```
Flask awaits `zephyr:res:{id}` with a 5s timeout and returns 504 on miss.

**Non-negotiable:** the bot re-validates `actor_id`'s guild permissions against the live
Discord cache before executing. Web-side permission checks are UX, not security.

---

## 3. Data model

```
guilds              (id PK, prefix, locale, timezone, default_volume,
                     dj_role_id, music_channel_ids[], enabled_cogs[], created_at)
ai_settings         (context_key PK, data JSON, updated_at)
                    -- opaque legacy keys: SERVER-{id}, DM-{id}, bare IDs, and older shapes
web_users           (discord_id PK, username, avatar_hash,
                     refresh_token_enc, token_expires_at, last_login_at)
bot_users           (discord_id PK, default_city, lat, lon, units, timezone)

weather_subs        (id PK, guild_id FK, channel_id, kind, location,
                     schedule_local_time, tz, thresholds JSONB,
                     enabled, last_run_at, last_fingerprint)
                    -- kind: 'daily' | 'severe' | 'class_suspension'

playlists           (id PK, owner_id, guild_id, name, is_public, created_at)
playlist_tracks     (playlist_id FK, position, title, url, duration_s, source)
                    -- PK (playlist_id, position)

ai_conversations    (id PK, channel_id UNIQUE, rolling_summary, token_count, updated_at)
ai_messages         (id PK, conversation_id FK, role, content, tokens, created_at)
personas            (id PK, guild_id FK, name, system_prompt, is_default)

audit_log           (id PK, guild_id, actor_id, action, payload JSONB,
                     source, created_at)   -- source: 'web' | 'discord'
```

Migration: a one-shot importer reads existing `settings.json` (and Redis, if
`REDIS_URL` was set) into `ai_settings`. **`zephyr/services/storage.py` keeps its
current interface**; two chat handlers defer and move database writes off the event loop.

---

## 4. API surface (`/api/v1`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/weather?lat&lon&units` | — | Open-Meteo: current + hourly(48) + daily(7) + AQI |
| GET | `/geocode?q` | — | Open-Meteo geocoding (drops Nominatim + `TimezoneFinder`) |
| GET | `/commands` | — | `help_data.py` exported to JSON — powers the ⌘K palette |
| GET | `/status` | — | from `zephyr:presence` |
| GET | `/auth/login` → `/auth/callback` | — | OAuth code flow, `state` CSRF, scopes `identify guilds` |
| POST | `/auth/logout` | session | |
| GET | `/me` | session | user + manageable guilds where the bot is present |
| GET/PATCH | `/guilds/:id/settings` | MANAGE_GUILD | |
| GET/PATCH | `/guilds/:id/ai` | MANAGE_GUILD | model, output, persona |
| GET | `/guilds/:id/player` | member | from `zephyr:player:{id}` |
| POST | `/guilds/:id/player/:action` | member + DJ | play/pause/skip/seek/volume/effects → bridge |
| CRUD | `/guilds/:id/weather-subs` | MANAGE_GUILD | |
| CRUD | `/playlists` | owner | + `POST /playlists/import/spotify` |
| GET | `/guilds/:id/ai/usage` | MANAGE_GUILD | RPM/TPM/RPD from the existing tracker |
| GET | `/guilds/:id/audit` | MANAGE_GUILD | paginated |

Session: server-side, Redis-backed, `HttpOnly` + `Secure` + `SameSite=Lax`.
Discord tokens are Fernet-encrypted at rest and **never** reach the browser.
CSRF token required on all mutating calls. Per-session rate limit on `/player/*`.

---

## 5. Frontend

```
website/frontend/
├── src/
│   ├── styles/theme.css        # @theme tokens: materials, shadows, radii, type, springs
│   ├── lib/{api.ts,haptics.ts,springs.ts,query.ts}
│   ├── types/api.ts            # hand-mirrored Flask contracts
│   ├── components/ios/         # the design system (see below)
│   ├── components/widgets/     # each widget = {id, size, render}
│   ├── routes/
│   │   ├── weather/            # public PWA
│   │   ├── dashboard/          # widget grid
│   │   └── g/$guildId/         # overview, music, ai, weather-alerts, settings, logs
│   └── store/{layout,prefs}.ts # Zustand + localStorage
└── vite.config.ts              # outDir '../static', dev proxy → Flask :5000
```

### iOS primitive layer (`components/ios/`)

`GlassSurface` · `PressableButton` · `Sheet` (detents + drag-dismiss) · `SegmentedControl` ·
`ListGroup`/`ListRow` (inset grouped) · `Toggle` · `Slider` · `Stepper` · `LargeTitleHeader`
(scroll-collapse) · `TabBar` (safe-area) · `DynamicIsland` · `CapsuleToast` ·
`PullToRefresh` · `WidgetGrid` (long-press → jiggle → dnd-kit reorder)

A `/kitchen-sink` dev route renders every primitive in both themes for review before
any feature work is built on top.

### Design tokens — the parts that matter

```css
@theme {
  /* Materials — blur AND saturation differ, not just opacity */
  --material-thin:    blur(20px) saturate(180%);
  --material-regular: blur(40px) saturate(150%);
  --material-thick:   blur(60px) saturate(120%);

  /* Shadows are stacked. A single shadow is the #1 fake-iOS tell. */
  --shadow-card: 0 1px 2px rgb(0 0 0/.04),
                 0 8px 24px rgb(0 0 0/.08),
                 0 24px 48px rgb(0 0 0/.06);

  /* Radii — shadcn's 0.5rem default is far too tight */
  --radius-control: 12px;
  --radius-card:    22px;
  --radius-sheet:   38px;

  --font-sans: -apple-system, BlinkMacSystemFont, 'Inter Variable', sans-serif;
  --text-body: 17px;   /* iOS body is 17, not 16 */
  --text-title-lg: 34px;
}
```

Springs, not easing — every shadcn `transition-*` duration gets replaced:
`tap {stiffness: 400, damping: 30}` · `sheet {stiffness: 220, damping: 26}` ·
`island {stiffness: 300, damping: 28}`

Rules that carry the illusion:
- **A real animated backdrop layer is required** — `backdrop-filter` over a flat
  background is invisible. Condition × local-time gradient mesh; optional canvas
  precipitation particles.
- **Press, not hover.** `whileTap={{scale: 0.96}}`; hover effects behind `@media (hover: hover)`.
- **Dark mode is layered, not inverted:** `#000` base → `#1C1C1E` → `#2C2C2E` → `#3A3A3C`.
  Glass in dark mode is *lighter* than its backdrop.
- **Sheet presentation:** page behind scales to `0.94` and gains corner radius.
- `corner-shape: squircle` where supported (Chromium), `border-radius` fallback.
- Honor `prefers-reduced-motion` **and** `prefers-reduced-transparency` — the latter
  swaps glass for solid surfaces, which doubles as the perf escape hatch.
- PWA: `apple-mobile-web-app-capable`, `viewport-fit=cover`,
  `env(safe-area-inset-bottom)` on the tab bar, maskable icons.
- Haptics: `navigator.vibrate(8)` tap / `15` toggle / `[10,40,10]` error.

### Signature interactions
1. Large-title collapse into a glass nav bar (`useScroll` + `useTransform`)
2. Sheets with 40%/92% detents and velocity-based dismiss
3. **Dynamic Island** — expands for now-playing, severe-weather alerts, command results
4. **Widget grid** — 2×2 / 4×2 / 4×4 on a 4-col grid, long-press jiggle, `+` gallery sheet
5. Segmented control with a `layoutId` sliding pill
6. Control-Center toggle grid for guild settings
7. Rubber-band overscroll + pull-to-refresh

---

## 6. Phases

Tracked as tasks #1–#8 in the session task list. Dependency graph:

```
#1 Phase 0 (data layer) ─┐
                         ├──▶ #4 Phase 3 (auth) ──┬──▶ #5 Phase 4 (music)   ─┐
#2 Phase 1 (frontend) ──▶ #3 Phase 2 (weather PWA)┤    #6 Phase 5 (weather) ─┼──▶ #8 Phase 7
                                                  └──▶ #7 Phase 6 (AI)      ─┘    (hardening)
```

| # | Phase | Blocked by | Ships |
|---|---|---|---|
| 1 | 0 — Data layer | — | Settings survive a restart |
| 2 | 1 — Frontend foundation | — | `/kitchen-sink` design-system review |
| 3 | 2 — Public weather PWA | #2 | **First user-visible release** |
| 4 | 3 — Auth | #1, #3 | Login + guild settings |
| 5 | 4 — Music | #4 | Web remote + playlists |
| 6 | 5 — Weather alerts | #4 | Subscriptions + auto-announce |
| 7 | 6 — AI | #4 | `/summarize` + memory |
| 8 | 7 — Hardening | #5, #6, #7 | Production-ready |

**#1 and #2 are independent** — the data layer and the design system can be built in
parallel. #5, #6 and #7 are also mutually independent once auth lands.

**Phase 0 — Data layer** *(blocks everything)*
Postgres + sync SQLAlchemy 2.0 with `create_all()` (Alembic is deferred to Phase 3).
`settings.json`/Redis → `ai_settings` importer. `storage.py` interface remains sync.
Fixes the live persistence bug.

**Phase 1 — Frontend foundation**
Vite + TS + Tailwind v4 + shadcn, re-skinned. Token layer, backdrop, every `ios/`
primitive, `/kitchen-sink` review route. No features yet.

**Phase 2 — Public weather PWA** *(first shippable slice)*
`/api/v1/{weather,geocode,commands,status}`, Open-Meteo migration, widget grid,
⌘K command palette over 62 unique commands, PWA manifest + service worker.

**Phase 3 — Auth**
Discord OAuth, Redis sessions, `/me`, guild picker, read-only guild overview.

**Phase 4 — Music**
Redis bridge both directions. Bot: playlists + `/save` `/load`, Spotify import,
autoplay/radio, now-playing message with buttons + live progress bar.
Web: live queue, transport controls, playlist editor, DJ-role config.

**Phase 5 — Weather alerts**
Bot: `tasks.loop` runner, due-row selection with `FOR UPDATE SKIP LOCKED`, tz-aware
via `zoneinfo`, severe watcher on a 15-min loop deduped by fingerprint,
class-suspension auto-announce. Web: subscription CRUD + preview.

**Phase 6 — AI**
`/summarize last N`, per-channel memory with rolling summarization, personas.
Web: persona editor, token/quota dashboard, memory inspector + purge.

**Phase 7 — Hardening**
a11y pass (keyboard path for the widget grid — drag-and-drop needs a menu fallback),
perf pass (budget concurrent `backdrop-filter` elements), rate limiting,
audit log surfacing, tests for the bridge and the scheduler.

---

## 7. Risks & open items

- **Hosting cost is real.** The bot must run as a persistent worker; free tiers on most
  PaaS either don't offer workers or spin them down. Budget for one paid worker, or host
  the bot on a VPS. **Verify current free-tier terms before committing** — managed free
  Postgres offers frequently expire (Render's has historically been time-limited);
  Neon/Supabase are alternatives.
- **`vercel_handler.py` / `aws_lambda_handler.py` become dead weight.** Serverless can't
  hold the Redis subscription or a session-backed dashboard. Either delete them or
  restrict them to the public weather page and document that.
- **`backdrop-filter` is the perf cliff.** A dozen concurrent glass layers will jank
  mid-range Android. `prefers-reduced-transparency` is the escape hatch; also cap
  simultaneous glass elements per view.
- **Gemini free-tier RPM** will throttle `/summarize` on a busy server. Needs a per-guild
  cooldown and a work queue, not a naive per-invocation call. Also: 2.5 models can't
  combine most tools in one request, so search-grounding needs its own code path.
- **Bot restart loses player state** — snapshot keys expire in 60s, so the dashboard
  correctly shows "offline" rather than stale data. Presence heartbeat covers this.
- `TimezoneFinder()` and `Nominatim()` are currently constructed **per request** in
  `website/app.py`; `TimezoneFinder` loads a large dataset each time. Both disappear with
  the Open-Meteo migration.
- **yt-dlp fragility is unchanged** by any of this. Playlist persistence stores URLs, so
  saved playlists will rot as videos are removed — plan a re-resolve path.
