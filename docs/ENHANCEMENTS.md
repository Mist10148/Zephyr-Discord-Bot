# Zephyr Web — Enhancement Backlog

A prioritised, phased list of UX/UI work for the dashboard and the public site, written
to be picked up and implemented directly.

Read [DESIGN.md](DESIGN.md) for the design-system rules and [SCREENS.md](SCREENS.md) for
what each screen currently contains. **Every item here must respect the rules in
DESIGN.md** — tokens not literals, `data-glass` on frosted surfaces, hooks in `.ts`
files, specs in `test/` not `src/`, and any new primitive added to `/kitchen-sink`.

**Effort key:** S = under an hour · M = half a day · L = a day or more
**Backend:** ✅ = the API already returns everything needed · ⚠️ = needs Flask/bot work

Phase numbering continues [WEB_DASHBOARD_PLAN.md](WEB_DASHBOARD_PLAN.md) §6, which ends at
Phase 7 (hardening).

---

## Where things stand

**Sections A–F (appendix) are the original API-surfacing backlog and are shipped**, apart
from three backend items. That work was about rendering data the API already returned.
What remains is a different class of problem: controls that do not work, actions that give
no feedback, machine values shown raw, and a layout that only really works at phone width.

| Phase | Theme | Status |
|---|---|---|
| A–F | Surface unused API data, code-splitting, PWA, a11y basics | Shipped, except **A3**, **F4**, **E5** |
| **8** | **UX correctness — dead and misbehaving controls** | **Not started** |
| **9** | **Feedback layer and display vocabulary** | **Not started** |
| **10** | **Layout, density and loading states** | **Not started** |
| **11** | **Navigation and information architecture** | **Not started** |
| **12** | **Public site layer** | **Not started** |

**Do Phase 8 first.** Those are bugs a user hits on the two most-used screens.

---

# Phase 8 — UX correctness

Dead controls and controls that behave wrongly. None of these need a design decision; each
has one correct answer. All six are S-sized and concentrated on `/weather` and
`/g/:id/music`.

### 8.1 — The queue's "Play" button does nothing · S · ✅

[`routes/GuildMusic.tsx`](../website/frontend/src/routes/GuildMusic.tsx) — `QueueRow`
renders `<PressableButton className="small soft">Play</PressableButton>` with **no
`onClick`**. Every row in the live queue carries a dead button.

**Fix:** either wire it to `run('play', { query: track.url ?? track.title, mode: 'next' })`
— jump-to-this-track expressed with the endpoints that exist — or delete it. Do not leave a
third state. If wired, relabel it "Play next" so it does not promise an absolute jump the
bridge cannot perform.

**Done when:** no button in the queue row is inert, and its label matches its effect.

### 8.2 — Effects sliders fire one mutation per drag step · S · ✅

[`routes/GuildMusic.tsx`](../website/frontend/src/routes/GuildMusic.tsx) — the bass-boost
and pitch sliders call `run('effects', …)` directly from `onChange`. Dragging pitch across
`0 → 2` at `step .1` issues a request per step, so the control feels like mud and one drag
can consume most of `PLAYER_RATE_LIMIT` (30 per window, see
[`website/api/player.py`](../website/api/player.py)) and start returning 429s.

**Fix:** use the draft-then-commit pattern already in the same file — local state wins until
the mutation settles, `onCommit` sends it. `Slider` already accepts `onCommit`; the volume
row and `ProgressBar` both do this correctly. Copy them.

**Done when:** one drag produces one request, and each effects row shows its current numeric
value the way the volume row shows `50%`.

### 8.3 — Geocode results show "No matching places" while still loading · S · ✅

[`routes/Weather.tsx`](../website/frontend/src/routes/Weather.tsx) — the results ternary
tests `places.data?.results?.length`, which is falsy during the fetch, so **every keystroke
flashes a false "No matching places"**. The same query also fires once per character typed.

**Fix:** branch on `places.isPending` before the empty case, and debounce the input by
~250ms — key the query on the debounced value, not the raw one, or react-query still sees a
new key per character.

**Done when:** typing a city goes loading → results and never shows a false empty state,
and the network panel shows roughly one request per pause rather than per keystroke.

### 8.4 — "Use my location" fails silently · S · ✅

[`routes/Weather.tsx`](../website/frontend/src/routes/Weather.tsx) — `locate()` passes
`() => undefined` as the geolocation error callback. Deny the browser permission, or let it
time out, and the button is permanently inert with no explanation.

**Fix:** hold the error in state and surface it through the 9.1 toast host (an `ErrorNote`
until that lands), distinguishing *denied* ("Location permission is off for this site")
from *unavailable* ("Could not get a fix — search for your city instead"). Pass
`{ timeout: 10000 }` as well; the default is no timeout at all.

**Done when:** denying permission produces a visible, actionable message.

### 8.5 — The weather page has no error state · S · ✅

[`routes/Weather.tsx`](../website/frontend/src/routes/Weather.tsx) — the forecast block is
gated on `place && weather.data` and `weather.isError` is never read, so a failed
`/weather` call renders **nothing at all** below the search field. Every authenticated
screen handles this with `ErrorNote`; the most-visited public page does not.

**Fix:** add the `weather.isError` branch with
`<ErrorNote error={weather.error} onRetry={() => weather.refetch()} />`, matching
[`routes/GuildMusic.tsx`](../website/frontend/src/routes/GuildMusic.tsx). The geocode query
needs the same treatment.

**Done when:** a forced 500 on `/weather` shows a message and a working Try again.

### 8.6 — Saved weather places cannot be removed · S · ✅

[`routes/Weather.tsx`](../website/frontend/src/routes/Weather.tsx) — `choose()` writes up to
six places into `localStorage['zephyr-weather-places']` and nothing ever deletes them. They
also render as `PressableButton variant="secondary"`, visually identical to the **"Use my
location"** action sitting in the same row, and nothing marks which one is being shown.

**Fix:** give saved places their own chip treatment with an `×`, separate them from the
action button, and mark the active one (`aria-current="true"` plus the accent token).

**Done when:** a saved place can be removed, and action and data are visually distinct.

---

# Phase 9 — Feedback layer and display vocabulary

The largest single UX gap in the app, plus the raw machine values that leak through it.

### 9.1 — There is no transient feedback anywhere · M · ✅

**Highest-value item in this document.**

`CapsuleToast` is a *static inline block*, not a notification, and there is no toast host.
It has two real call sites (one error note, one advisory line). Consequences:

- Queueing a track in [`routes/GuildMusic.tsx`](../website/frontend/src/routes/GuildMusic.tsx)
  clears the input and **nothing else happens** — confirmation arrives whenever the 3s
  player poll next lands. Every toggle, save and delete in the app is equally silent.
- Errors render *in document flow* mid-page: the player's `ErrorNote` is injected between
  the effects panel and the queue heading, so a failure shoves the page around.
- The undo affordance is a hand-rolled `<div className="toast success">` placed inline
  **after** the queue list. With a long queue it spends its entire 5-second life below the
  fold — so D3's "undo instead of confirm" is implemented but unreachable in practice.

**Fix:** one fixed-position toast region (top-right on desktop, above the tab bar on
mobile) plus a `useToast()` hook in a `.ts` file, rendered once from `AppShell`. Route
mutation `onSuccess`/`onError` through it. Keep `CapsuleToast` as the visual — it already
has the three tones and switches `role` correctly. Move the music undo into it. Stack at
most three, auto-dismiss neutral and success, keep errors until dismissed, and give the
region `aria-live="polite"` while errors keep `role="alert"`.

**Done when:** every mutation on every screen produces visible confirmation or visible
failure, no success/error state changes page layout, and `/kitchen-sink` demonstrates the
host with toasts stacked.

### 9.2 — Weather numbers carry no units · S · ✅

[`routes/Weather.tsx`](../website/frontend/src/routes/Weather.tsx) renders `Wind {…}`,
`Rain {…}` and `wind {day.wind_speed_max}` as bare numbers. C8 added a metric/imperial
preference, so "Wind 12" is now genuinely ambiguous — km/h or mph, mm or inches.

**Fix:** a `formatUnit` helper in `lib/` keyed off `preferences.units`, used for wind,
precipitation and every other dimensioned value. Temperatures already carry `°`; add the
scale to the current-conditions card so the page declares its system once.

**Done when:** no dimensioned number renders without its unit in either system.

### 9.3 — Raw Discord snowflakes on the server overview · M · ⚠️

[`routes/GuildOverview.tsx`](../website/frontend/src/routes/GuildOverview.tsx) — the
Configuration list prints `dj_role_id` and a comma-joined `music_channel_ids` as bare
19-digit numbers, and `enabled_cogs` as raw Python module names.

This is the same defect as **A3** (`actor_id` in the audit log) on a screen A3 does not
cover; do them together. `GuildSettings` already resolves channel names through the bridge,
so the lookup exists — either extend `GET /guilds/<id>` to include resolved names or reuse
the settings channel/role query here. Fall back to the raw id when the bot is unreachable,
the way the pickers already do. Map cog keys to the titles `GET /commands` already returns.

Long channel lists must also stop overflowing: `.row-value` is `flex: 0 0 auto` with no
wrapping, so three channels push the row apart.

**Done when:** the overview shows names, not ids, and degrades to ids only when the bridge
is down.

### 9.4 — Audio effect names are unmapped identifiers · S · ✅

[`routes/GuildMusic.tsx`](../website/frontend/src/routes/GuildMusic.tsx) labels effects with
`effect.replace('_', ' ')`, so the UI reads "sixteen d" and "slownrev".

**Fix:** an explicit label map — `sixteen_d` → "16D Audio", `slownrev` → "Slowed + Reverb",
and so on — plus a one-line `detail` per row saying what the effect does. These are
unguessable from the name.

**Done when:** no effect row shows a snake_case identifier.

### 9.5 — Now-playing art placeholder prints its own name · S · ✅

[`routes/GuildMusic.tsx`](../website/frontend/src/routes/GuildMusic.tsx) — the fallback is
`<span className="art-placeholder" aria-hidden>track art</span>`, which renders the literal
words "track art" where the thumbnail should be. Replace with an icon from
[`components/icons.tsx`](../website/frontend/src/components/icons.tsx) on a `--surface-2`
tile.

### 9.6 — The hourly strip uses text emoji · S · ✅

[`routes/Weather.tsx`](../website/frontend/src/routes/Weather.tsx) draws `☀ ☂ ☁` as text
inside the hour cards while the rest of the app uses the `currentColor` SVG set. Those
render as platform-specific colour glyphs — off-palette in both themes, different on every
OS — and DESIGN.md commits to the icon set explicitly.

**Fix:** reuse `DayGlyph`, which is in the same file and already maps `weatherGlyph(code)`
to the three SVGs.

**Also in the strip:** the precipitation bars are `<i style={{ height: '<n>%' }} />` with no
baseline or scale, so a bar means nothing on its own — put a hairline track behind each so
the height reads against a full-scale reference. Their `title` sits on an `aria-hidden`
element where nobody will hear it; move the number into the accessible name of the hour
cell.

---

# Phase 10 — Layout, density and loading states

A well-built phone layout currently being stretched to 1600px.

### 10.1 — List rows are unreadable at desktop width · M · ✅

[`styles/theme.css`](../website/frontend/src/styles/theme.css) — `.app` is
`max-width: 1600px` and every screen is a single column. `.row-label` is `flex: 1 1 auto`
and `.row-value` is `flex: 0 0 auto; text-align: right`, so on a wide monitor a label sits
at the far left and its value at the far right with ~1400px of nothing between them; the
eye cannot associate the pair. This hits the overview facts list, settings, audit rows and
every `ListGroup` in the app.

Widening the shell widened the gaps *inside rows*, which is the opposite of the intent.

**Fix:** cap the measure of row-based content — `.list-group { max-width: 760px }` behind a
token so it is set once — while leaving grids (`widget-grid`, `day-grid`, the hourly strip)
free to use the full width.

**Done when:** at 1600px every label/value pair reads as a pair, and grids still fill the
shell.

### 10.2 — Music is one flat scroll of nine control groups · M · ✅

[`routes/GuildMusic.tsx`](../website/frontend/src/routes/GuildMusic.tsx) stacks search → now
playing → transport → loop/volume/autoplay → eight effects → queue → playlists in one
column at every width. On desktop that is a very long scroll with the queue permanently
below the fold while half the viewport sits empty.

**Fix:** two columns at ≥1200px — player, transport and effects left; search, queue and
playlists right. The 860px breakpoint stays as it is; this is a third, wider arrangement,
not a change to the existing one. Reuse the `GuildShell` grid rather than inventing a
second layout mechanism.

**Done when:** at 1440px the queue is visible without scrolling past the player.

### 10.3 — Skeletons still have one generic shape · S · ✅

D7 is marked shipped, but `Skeleton` in
[`components/ios/index.tsx`](../website/frontend/src/components/ios/index.tsx) is still a
single component rendering `lines` identical bars, and the two heaviest screens
(`GuildMusic`, `GuildOverview`) plus the route-level Suspense fallback in `App.tsx` all call
`Skeleton lines={6}`. The layout still jumps on load.

**Fix:** shaped variants matching the real layouts — card grid, list rows, now playing. Add
each to `/kitchen-sink`.

**Done when:** loading `GuildMusic` and `GuildOverview` causes no visible reflow once data
arrives.

---

# Phase 11 — Navigation and information architecture

Four S-sized fixes and one M.

### 11.1 — `/commands` is unreachable · S · ✅

The command reference — 73 commands, shipped as **C10** — has **no entry point in the UI**.
It is absent from `TABS` in [`components/TabBar.tsx`](../website/frontend/src/components/TabBar.tsx),
from the nav in [`components/AppShell.tsx`](../website/frontend/src/components/AppShell.tsx),
and from `FEATURES` in [`routes/Home.tsx`](../website/frontend/src/routes/Home.tsx) — which
instead advertises the internal design-system page as one of three cards. Only the ⌘K
palette can reach it.

**Fix:** add it to the top-bar nav and the home feature grid. Taking the `/kitchen-sink`
slot is the natural swap — see 11.4.

### 11.2 — One destination, two names · S · ✅

`/settings` is labelled **"Appearance"** in the top bar and **"System"** in the tab bar.
Pick one. "Appearance" describes the contents; "System" does not.

### 11.3 — Back links on top-level destinations · S · ✅

`/weather` and `/commands` are primary tab destinations, and both end with
`<BackLink to="/">Back home</BackLink>` while the tab bar is showing Home. A back
affordance on a root destination reads as a bug. Keep `BackLink` on guild sub-pages, where
it is correct.

### 11.4 — The design-system page is a public headline feature · S · ✅

[`routes/Home.tsx`](../website/frontend/src/routes/Home.tsx) gives `/kitchen-sink` one of
three cards on the landing page, selling an internal review surface to prospective users.
The route stays — it is the design contract — but not as a third of the hero grid. Replace
that card with Commands or Music and demote `/kitchen-sink` to a footer or `/settings` link.

### 11.5 — `/commands` needs in-page navigation · M · ✅

[`routes/Commands.tsx`](../website/frontend/src/routes/Commands.tsx) renders 73 rows across
every category as one long scroll with no category jump list and no result count. Aliases
are in the Fuse search keys but never displayed, so searching an alias returns a hit with no
visible reason for the match.

**Fix:** a sticky category chip row that scrolls to each section, an "N of 73" count while
filtering, and each command's aliases in the row detail. Copy-to-clipboard on the row would
mirror what the palette already does with a selected command.

---

# Phase 12 — Public site layer

Everything above assumes a visitor already knows what Zephyr is. This phase is what makes
the deployment a website rather than a dashboard behind a URL. **12.1 and 12.2 are the only
items here that block anything real** — 12.2 blocks Discord app verification.

### 12.1 — A visitor cannot add the bot · S · ⚠️

`invite_url` is returned only by `GET /api/v1/me` and rendered only in
[`routes/Guilds.tsx`](../website/frontend/src/routes/Guilds.tsx) — behind auth. Someone
landing on `/` is offered "Check the weather" and "Open dashboard" and no way to install
Zephyr. For a bot site that is the primary conversion action.

**Fix:** include the invite URL in the unauthenticated `GET /api/v1/status` response,
derived from `DISCORD_CLIENT_ID` + `DISCORD_INVITE_PERMISSIONS` exactly as
[`website/api/me.py`](../website/api/me.py) does, and make "Add Zephyr to Discord" the
primary hero button.

### 12.2 — No Privacy Policy or Terms · M · ⚠️

Neither exists anywhere in the repo. Discord requires both URLs for app verification, and
the service genuinely processes personal data: Discord user ids, OAuth tokens in Redis,
per-channel AI conversation memory, and audit rows carrying `actor_id`.

**Fix:** `/privacy` and `/terms` routes plus a documented data-deletion path — ideally
self-service, at minimum a stated contact route. The AI memory purge already implements
deletion for the largest data category, so describe it. Link both from the footer (12.4)
and register them on the Discord application.

### 12.3 — No link previews, and one title for every route · S · ✅

[`website/frontend/index.html`](../website/frontend/index.html) has no Open Graph or Twitter
card tags, so a link to the site pasted **into Discord** renders as a bare URL. For a
Discord bot that is the highest-leverage missing markup on the site.

**Fix:** `og:title`/`description`/`image`/`url` and `twitter:card=summary_large_image`, with
a 1200×630 image generated from the design tokens the way
[`scripts/generate_icons.py`](../scripts/generate_icons.py) generates the PWA icons.

Separately, every route shares one static `<title>` ("Zephyr Weather"), so browser history
and tabs cannot tell `/commands` from `/weather`. Add per-route title, description and
canonical.

### 12.4 — No footer · S · ✅

No support-server link, no repository link, no legal links, no copyright. This is where most
of the "is this a real product" signal lives, and where 12.2's pages get linked from.

### 12.5 — Crawlers and soft 404s · M · ✅

No `robots.txt` or `sitemap.xml` in
[`website/frontend/public/`](../website/frontend/public/) — add both, disallowing `/g/*`,
`/login` and `/kitchen-sink`. The SPA is lazy-loaded with no prerendering, so a crawler gets
an empty shell; build-time prerendering of the four public routes is the right scale of fix,
not SSR.

Separately, [`website/spa.py`](../website/spa.py) serves `index.html` with **HTTP 200** for
every unknown path, so `/nonsense` is an indexable soft 404 that renders `NotFound`. Return
404 for paths outside the known route table.

### 12.6 — Public endpoints are unguarded · M · ⚠️

`rate_limit()` in [`website/api/guard.py`](../website/api/guard.py) is used by
[`website/api/player.py`](../website/api/player.py) only. On a public origin `/weather` and
`/geocode` (which spend an upstream Open-Meteo quota), `/ai/*` (a free-tier Gemini key) and
`/auth/login` are all unlimited.

**Fix:** per-IP limits on the public read endpoints and a tighter one on `/auth/login`.
`TRUST_PROXY` and `ProxyFix` are already wired, so the client address is trustworthy behind
Render.

### 12.7 — Operational blind spots · M · ⚠️

No error tracking (a production 500 is invisible), no analytics (no way to know whether
anyone visits), no uptime monitoring. Render's free web tier also spins down, so a cold
visitor sees the home status pill read "Bot offline" while the service wakes — the pill
should distinguish *waking* from *offline*.

If analytics are added, choose a cookieless option so no consent banner is needed, and note
that `connect-src 'self'` in [`website/security.py`](../website/security.py) must then be
widened deliberately.

---

## Suggested order

1. **Phase 8** — the six correctness bugs. All S, all unambiguous.
2. **9.1** — the toast host. Everything after it gets to use it, and 8.4 wants it.
3. **9.2–9.6** — units and vocabulary; the cheapest visible quality gain in the app.
4. **10.1** — the list measure cap. One rule, fixes every screen on desktop.
5. **11.1–11.4** — four S-sized IA fixes.
6. **12.1–12.4** — the public layer. 12.2 blocks Discord verification, so start it before
   you need it.
7. **10.2, 10.3, 11.5, 12.5–12.7** — by appetite.
8. **A3, F4, E5** — the three original backend items, still open.

---

# Appendix — Sections A–F (shipped)

Retained for reference. These were about surfacing data the API already returned; all are
implemented except **A3** (audit actor-name lookup), **F4** (server-side audit filters) and
**E5** (SSE player stream), which remain backend work and are folded into the ordering
above.

**A. Correctness gaps** — A1 destructive-action confirmations (`ConfirmSheet`, four call
sites) · A2 command palette navigates and copies · A3 ⚠️ audit actor names · A4 audit
`payload` detail.

**B. Unused API data** — B1 hourly strip · B2 richer current conditions · B3 full air
quality · B4 AI token totals and `cooldown_until` banner · B5 queue duration · B6
subscription `last_run_at` · B7 `bot_snapshot_at` staleness · B8 playlist `is_public`.

**C. Features on existing endpoints** — C1 search and queue from the web · C2 scrubbable
progress bar · C3 drag-reorder the live queue · C4 clear queue · C5 effects panel · C6 edit
persona · C7 alert thresholds · C8 imperial units · C9 memory transcript · C10 command
reference page.

**D. Accessibility and interaction** — D1 player `aria-live` · D2 skip link · D3 undo toast
*(shipped but unreachable — see 9.1)* · D4 optimistic toggles · D5 Light/System/Dark · D6
error boundary and offline banner · D7 shaped skeletons *(partial — see 10.3)*.

**E. Performance and PWA** — E1 route code-splitting · E2 prefetch on hover · E3 service
worker update prompt · E4 offline fallback · E5 ⚠️ SSE player stream.

**F. Bigger features** — F1 guild switcher · F2 persistent mini-player · F3 saved locations
and geolocation *(see 8.4, 8.6)* · F4 ⚠️ audit filtering · F5 dashboard activity feed.
