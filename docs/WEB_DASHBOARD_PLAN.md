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

---

## 8. Implementation notes & deviations

§§1–7 above are the specification. This section is the changelog *against* it: where the shipped
code differs and why. It is appended to as phases land, and §§1–7 are not rewritten.

### Phase 3 — Auth (backend)

1. **§4 "manageable guilds where the bot is present" → annotate, don't filter.** `/me` returns
   every manageable guild with `bot_present: true | false | null` plus an `invite_url`. Silently
   hiding a server the user administers is an unexplainable dead end, and filtering is outright
   wrong when no snapshot has been published — it would hide everything. `null` (never published)
   is kept distinct from `false` (published, bot absent).
2. **§4 "Discord tokens are Fernet-encrypted at rest" → no tokens are stored at all.** Nothing
   through Phase 6 calls Discord as the user while the user is absent; the access token's whole
   lifetime is two calls inside `/auth/callback`. `web_users.refresh_token_enc` and
   `token_expires_at` ship per §3 and stay `NULL`, so the table shape is final. The second clause
   of the spec ("never reach the browser") holds strictly; the first holds vacuously, which is a
   stronger property than encryption. `cryptography` is therefore not a dependency yet. The
   consequence — a session's guild list can only be refreshed by re-running OAuth — is handled by
   `guilds_stale` in `/me` plus `prompt=none`, making the refresh a silent redirect round trip.
3. **§4 CSRF → synchronizer token, delivered by cookie *and* payload.** The token is minted with
   the session and stored in Redis. It is mirrored into a readable `zephyr_csrf` cookie and also
   returned as `csrf_token` from `/me`; the client echoes it in `X-Zephyr-CSRF`. Validation always
   compares the header against the *session's* stored value, never against the cookie, so the
   cookie needs no signing and **no `SECRET_KEY` is required anywhere**. Leaving `app.secret_key`
   unset is deliberate: any future accidental `flask.session` use then fails loudly.
4. **§4 `SameSite=Lax` is mandatory, not preferential.** `Strict` is dropped on the cross-site
   top-level redirect back from `discord.com`, so the session would vanish exactly once, in a way
   that looks like a random bug.
5. **§3 `audit_log` → omitted.** No writer exists until the first mutating guild endpoint, and its
   exact shape (does `payload` need before/after? an index on `(guild_id, created_at)`?) would be
   relitigated the moment that arrives.
6. **§3 array columns → `JSON`, not `postgresql.ARRAY`.** `ARRAY` is Postgres-only and
   `DEFAULT_DATABASE_URL` is SQLite, so it would break local development and every test.
7. **§3 snowflake columns → `String`, not `BigInteger`.** They exceed JavaScript's
   `Number.MAX_SAFE_INTEGER`, so the API must emit strings regardless; storing strings removes a
   conversion at every boundary and matches `ai_settings.context_key`.
8. **§6 "Alembic is deferred to Phase 3" → included, but not automatic.** No `alembic upgrade head`
   in `render.yaml`'s `startCommand`: two gunicorn workers would race it and the free tier has no
   release phase. `create_all()` stays as the development path. Documented manual step; automation
   is a Phase 7 item, as is a model-vs-migration drift check.
9. **§4 `GET/PATCH /guilds/:id/settings` → this phase ships `GET /api/v1/guilds/<id>`.** A distinct
   resource, so the editable settings endpoint lands later with no rename and no shadowed route.
10. **§2's diagram implies Flask↔Redis pub/sub — not in this phase.** Only `SET`/`GETEX`/`GETDEL`/
    `MGET`. The bot-side `zephyr:guilds` snapshot writer is *not* the Phase 4 bridge: no pub/sub, no
    `zephyr:cmd` channel, no response correlation. Worth stating because the snapshot touches the
    bot process and could be mistaken for the bridge landing early.
11. **`zephyr:guilds` has no TTL**, unlike the presence and player keys. Stale liveness is worse
    than none, but stale *membership* is far better than none: expiring it while the bot is briefly
    down would empty the guild picker. Every bot start rewrites it, and `zephyr:guilds:updated_at`
    exposes the staleness bound.
12. **Session failures raise; settings failures don't.** `website/session.py` deliberately does not
    copy `RedisStorage`'s `except Exception: print(...)`. That soft-fail is right for settings and
    catastrophic for sessions — a Redis blip would look like a silent logout and a failed write like
    a successful login. `RedisStorage` itself is left untouched: different process, different
    encoding contract.
13. **A blueprint-scoped `before_request` does not run for a 405**, because no blueprint matched the
    request. Harmless (a 405 executes no handler) but it means the CSRF guard covers registered
    routes rather than arbitrary method mismatches.
14. **For whatever CSP lands in Phase 7:** it must include
    `img-src 'self' https://cdn.discordapp.com data:` or the guild picker renders blank. And
    `runtimeCaching` must never be added for authenticated endpoints — Cache Storage is readable by
    any script on the origin and survives logout.

### Phase 3 — Auth (frontend)

15. **§5's `routes/g/$guildId/` and `routes/dashboard/` → flat `src/routes/*.tsx`.** `$guildId` is a
    file-based-router convention (TanStack Router, Remix). This app uses react-router's declarative
    `<Routes>`, where there is no file routing, so those directories would each hold one file of
    pure ceremony.
16. **§5's `store/{layout,prefs}.ts` (Zustand) → not introduced.** Auth is server state and the
    session cookie is `HttpOnly`, so "am I signed in?" is literally a server query — which is what
    TanStack Query already is here. `zustand` stays an unused dependency.
17. **§5's `TabBar`, large-title scroll-collapse, `DynamicIsland`, `PullToRefresh` → still
    unwired,** and no layout route or `<Outlet>`. Each page renders its own `<main className="app">`,
    so a shell means rewriting all of them, and `.tab-bar` is currently a malformed padding
    shorthand with no item styling and no icon set. That is a design-system change, not an auth one.
18. **§1's "Polling first (`refetchInterval: 3000`)" → not applied** to `/me` or the guild overview.
    Both are static; polling belongs to the player snapshot.
19. **A live PWA bug was fixed here, not introduced.** The shipped service worker registered
    `NavigationRoute` with no `navigateFallbackDenylist`, so every same-origin navigation — including
    `/api/v1/auth/login` and Discord's callback — was answered from the cached shell and never
    reached Flask. OAuth would have silently failed for anyone whose worker had activated. `scope`
    is now explicit too: in an installed PWA the callback must be inside scope, or the redirect
    leaves the standalone window for the system browser, where the cookie lands in a different jar.
20. **`react-refresh/only-export-components` is handled by file placement, not config.** The rule
    only scans `.jsx`/`.tsx`, so hooks live in `lib/auth.ts` and helper functions stay module-local
    inside `.tsx` files. CI treats warnings as failures.
21. **A 503 `auth_not_configured` is treated as a deployment state, not an outage.** `RequireAuth`
    sends it to `/login?error=not_configured` rather than to a retry button that can never succeed —
    the first thing a self-hoster hits before setting up an OAuth application.
22. **No frontend test runner was added.** Vitest plus testing-library plus jsdom is four dev
    dependencies, lockfile churn, a CI step and a tsconfig reference. It belongs with the hardening
    phase, which already promises tests.
