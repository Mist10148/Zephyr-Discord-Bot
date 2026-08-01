# Zephyr Web — Enhancement Backlog

A prioritised list of UX/UI improvements and features for the dashboard, written to be
picked up and implemented directly.

Read [DESIGN.md](DESIGN.md) for the design system rules and [SCREENS.md](SCREENS.md) for
what each screen currently contains. **Every item here must respect the rules in
DESIGN.md** — tokens not literals, `data-glass` on frosted surfaces, hooks in `.ts`
files, specs in `test/` not `src/`, and any new primitive added to `/kitchen-sink`.

**Effort key:** S = under an hour · M = half a day · L = a day or more
**Backend:** ✅ = the API already returns everything needed · ⚠️ = needs Flask/bot work

---

## A. Correctness gaps

These are arguably bugs rather than enhancements. Do these first.

### A1 — Destructive actions fire with no confirmation · S · ✅
Deleting a **playlist**, a **weather subscription**, or an **AI persona** mutates
immediately on click. Only the AI memory purge asks first.

- `components/PlaylistPanel.tsx` — `remove.mutate(playlist.id)`
- `routes/GuildWeatherAlerts.tsx` — `remove.mutate(sub.id)`
- `routes/GuildAI.tsx` — `remove.mutate(persona.id)`

Reuse the `Sheet` confirmation pattern already in `GuildAI.tsx` (the purge flow). A
generic `<ConfirmSheet title description confirmLabel onConfirm />` is worth extracting,
since it will have four call sites.

### A2 — The command palette does nothing when you pick something · M · ✅
`components/CommandPalette.tsx` — `onSelect={() => onOpenChange(false)}`. It lists the
bot's 73 commands and selecting one just closes the dialog.

Decide what it is for, then make it do that. Suggested: make it a **navigation** palette
first (jump to any screen, switch server, toggle theme) with bot commands as a second
section that copies `/command` to the clipboard and confirms with a toast. Group results
using the `categories` array `/commands` already returns.

### A3 — The audit log never shows *who* · M · ⚠️
The page subtitle says "Who changed what in this server, and from where" and then shows
only what and where. `AuditEntry.actor_id` is fetched and never rendered.

Rendering the raw snowflake is not useful, so this needs a name lookup — either extend
`GET /guilds/<id>/audit` to resolve actor names server-side via the bridge, or add a
small members endpoint the page can join against. Fall back to the raw id when the bot
is unreachable, the way the channel pickers already do.

### A4 — `payload` is never surfaced · S · ✅
`AuditEntry.payload` holds what actually changed (e.g. which settings keys). Show it as
an expandable detail on the row — "Updated server settings" is much less useful than
"Updated server settings — prefix, timezone".

---

## B. Free wins — data the API already returns and nothing renders

Verified unused: `hourly`, `effects`, `queue_duration_s`, `is_public`, `totals`,
`cooldown_until`, `last_run_at`, `bot_snapshot_at`, `categories`, `actor_id`.

### B1 — Hourly forecast strip · M · ✅
`GET /weather` returns a full `hourly[]` array (48h by default) with temperature,
apparent temperature, humidity, precipitation probability and wind. The Weather screen
shows none of it.

Add a horizontally scrolling hour strip between the current card and the 7-day grid:
hour, glyph, temp, and a precipitation-probability bar. Note the field names are the raw
Open-Meteo ones (`temperature_2m`, `wind_speed_10m`), unlike `current`.

### B2 — Richer current conditions · S · ✅
`current` also carries `humidity`, `wind_speed` and `precipitation`; `daily[]` carries
`feels_like_max/min`, `precipitation_probability` and `wind_speed_max`. Add them as chips
on the current card and as a detail line per day card.

### B3 — Full air quality · S · ✅
`air_quality` returns `european_aqi`, `us_aqi`, `pm10`, `pm2_5`, `ozone` and
`nitrogen_dioxide`. Only the band string is shown. Make the chip open a small popover
with the pollutant breakdown.

### B4 — AI usage totals and cooldown · S · ✅
`GET /ai/usage` returns `totals` (prompt/output/total tokens, successful and session
request counts) and `cooldown_until`. The quota strip shows only RPM/TPM/RPD.

Add the token totals, and — importantly — surface `cooldown_until` as a warning banner
when set, since that is the state where the bot will refuse to answer.

### B5 — Queue duration · S · ✅
`Player.queue_duration_s` is fetched and unused. Show it beside the queue heading:
`Queue (12) · 47m`.

### B6 — Subscription last-run time · S · ✅
`WeatherSub.last_run_at` is unused. Add "Last posted 4h ago" to each subscription row —
it is the fastest way to tell a working subscription from a silently broken one.

### B7 — Bot snapshot age · S · ✅
`Me.bot_snapshot_at` is unused. When the presence snapshot is stale, the dashboard's
"Zephyr" card should say so rather than implying live data.

### B8 — Playlist visibility · S · ✅
`is_public` is returned and accepted by POST/PATCH but never exposed. Add a toggle in the
playlist editor sheet; it is what makes a playlist visible to others in the server.

---

## C. Features using endpoints that already exist

The UI can trigger 10 of the 15 whitelisted player actions. **Missing: `play`, `seek`,
`clear`, `move`, `effects`.**

### C1 — Search and queue music from the web · L · ✅
`POST /guilds/<id>/player/play` accepts `{query, mode}` and `mode: "next"` front-queues.
Today you can only control music that somebody already started in Discord.

Add a search field above the queue. Note the bot joins **the actor's own** voice channel
and a channel id in the body is deliberately not accepted — so the UI must explain "join
a voice channel in Discord first" and handle the 409 refusal that comes back if not.
This is the single biggest capability gap in the dashboard.

### C2 — Scrubbable progress bar · M · ✅
`seek` takes `{position}`. The progress bar is currently display-only. Make it an
`input[type=range]` styled as the bar, committing on release, with the local value
winning until the mutation settles — the same pattern `GuildMusic` already uses for
volume.

### C3 — Drag to reorder the live queue · M · ✅
`move` takes `{from, to}`. `PlaylistPanel` already has a working dnd-kit sortable list
with keyboard support — lift that into a shared `SortableList` and reuse it for the
queue.

### C4 — Clear queue · S · ✅
`clear` exists and returns `{removed: n}`. One button beside the queue heading, behind
the A1 confirmation.

### C5 — Audio effects panel · M · ✅
`effects` accepts `reset`, `pitch`, `bass_boost`, `nightcore`, `vaporwave`, `reverb`,
`slowed`, `slownrev`, `sixteen_d`, and `Player.effects` reports current state. None of it
is in the UI. A collapsible panel of toggles plus a pitch/bass slider and a Reset.

### C6 — Edit a persona · S · ✅
`PATCH /ai/personas/<id>` exists; the UI can only create and delete. Reuse the create
form in a Sheet, prefilled.

### C7 — Weather alert thresholds · M · ✅
`WeatherSub.thresholds` and `default_thresholds` (wind speed, precipitation probability,
apparent temperature, storm) are returned and accepted by PATCH, but there is no way to
edit them. Add a threshold section to the subscription sheet, defaulted from
`default_thresholds`.

### C8 — Imperial units · S · ✅
Both `GET /weather` and weather subscriptions accept `units: metric|imperial`. Nothing
exposes it. Add a preference (persist in `localStorage` next to the theme) and a per
subscription override.

### C9 — Channel memory transcript · M · ✅
`GET /ai/memory/<channel_id>` returns the conversation **plus its `messages`** (role,
content, tokens, timestamps). The list only shows counts. Make each row open a Sheet with
the transcript — it is the only way to see what the bot is actually remembering before
deciding to purge it.

### C10 — A browsable command reference · M · ✅
`GET /commands` returns 73 commands with `aliases`, `args`, `category`, `category_title`
and `emoji`. All of that is reachable only through the palette. A `/commands` page
grouped by category would give the public site a real second page.

---

## D. Accessibility and interaction polish

### D1 — Announce player state changes · S · ✅
Pause/skip/stop change state with no screen-reader feedback. Add an `aria-live="polite"`
region reflecting the now-playing track and play state.

### D2 — Skip to content link · S · ✅
No way to bypass the top bar by keyboard. Standard visually-hidden-until-focused anchor.

### D3 — Undo instead of confirm, where it fits · M · ✅
For queue removal specifically, an "Undo" toast is better than a confirmation dialog —
the action is cheap to reverse (`play` with `mode: "next"` or re-`move`). Keep A1's
confirmations for the genuinely destructive ones.

### D4 — Optimistic toggles · M · ✅
Toggles wait for a round trip, so they feel laggy on a slow link. Use react-query's
`onMutate` to flip immediately and roll back on error. Applies to autoplay and to
subscription enable/pause.

### D5 — A "System" theme option · S · ✅
The toggle is binary, but `theme-context.ts` already models "no stored choice = follow
OS". Make that a visible third state (Light / System / Dark) using the existing
`SegmentedControl`, so users can get back to auto after once choosing.

### D6 — Error boundary and offline indicator · M · ✅
A render error currently blanks the page. Add a route-level error boundary, plus an
offline banner driven by `navigator.onLine` — the app polls every 3s and silently fails
when the network drops.

### D7 — Shaped skeletons · S · ✅
`Skeleton` renders generic bars everywhere. Per-screen skeletons that match the final
layout (card grid, list rows) remove the layout jump on load.

---

## E. Performance and PWA

### E1 — Code-split the routes · M · ✅
The bundle is one **571 KB** chunk (183 KB gzip) and Vite warns about it on every build.
`React.lazy` per route, with the seven authenticated screens split from the public three,
would roughly halve first load for the visitors who only ever see the weather page.

### E2 — Prefetch on hover · S · ✅
Hovering a server card or nav item could prefetch its query. `queryClient.prefetchQuery`,
a few lines each.

### E3 — Service worker update prompt · S · ✅
`registerType: 'autoUpdate'` swaps the bundle silently, so a user can be mid-task when
assets change. Switch to `prompt` and show a "New version available — reload" toast.

### E4 — Offline fallback · M · ✅
The SPA shell is precached but every screen needs the API. Add an offline view and let
react-query serve last-known data where it is harmless (weather, command list).

### E5 — Replace polling with a live stream · L · ⚠️
`usePlayer` polls every 3s per open tab. An SSE endpoint on the Flask side, fed by the
Redis pub/sub the bridge already uses, would be both cheaper and more responsive. This is
the one item here with real backend design work.

---

## F. Bigger features

### F1 — Guild switcher in the rail · M · ✅
Changing server means going back to `/g` and picking again. A dropdown on the rail header
(which already shows the current server's icon and name) using the `guilds` array from
`/me`, which is already loaded.

### F2 — Persistent mini-player · M · ✅
Now-playing state disappears when you leave the Music screen. A compact bar above the tab
bar on mobile / bottom-right on desktop, visible across all guild screens when
`player.live`. The unused `DynamicIsland` primitive was removed, but this is what it was
for — reintroduce it deliberately, and add it to `/kitchen-sink`.

### F3 — Saved locations and geolocation · M · ✅
The Weather screen re-types a city every visit. Persist recent searches in `localStorage`
and add a "Use my location" button (`navigator.geolocation` → the existing lat/lon
query). Note `Permissions-Policy` in `website/security.py` already allows
`geolocation=(self)`.

### F4 — Audit filtering · M · ⚠️
Filter by action type, source and date. The endpoint currently only supports
`limit`/`before`, so server-side filtering needs new query params; client-side filtering
of the loaded pages is a cheaper first step.

### F5 — Dashboard activity feed · M · ✅
The new `/g` dashboard has room for a "recent activity" column drawing the newest few
audit entries across servers. Would need one request per guild today — consider a
combined endpoint if it feels slow.

---

## Suggested order

1. **A1–A4** — the correctness gaps, especially the missing confirmations.
2. **B5, B6, B7, B4** — small, high signal-to-noise wins.
3. **C1, C2, C4** — real music control from the web, the biggest capability gap.
4. **E1** — code splitting, before the bundle grows further.
5. **D5, D6, D1** — accessibility and resilience.
6. **F1, F2** — navigation and continuity.
7. Everything else by appetite.
