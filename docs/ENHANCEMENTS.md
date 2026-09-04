# Zephyr — Enhancement Backlog

A prioritised, phased list of the outstanding work across the whole project — the dashboard
and public site (Phases 8–12) and the Discord bot itself (Phases 13–17) — written to be
picked up and implemented directly.

Read [DESIGN.md](DESIGN.md) for the design-system rules and [SCREENS.md](SCREENS.md) for
what each screen currently contains. **Every web item here must respect the rules in
DESIGN.md** — tokens not literals, `data-glass` on frosted surfaces, hooks in `.ts`
files, specs in `test/` not `src/`, and any new primitive added to `/kitchen-sink`.

**Effort key:** S = under an hour · M = half a day · L = a day or more
**Backend:** ✅ = the API already returns everything needed · ⚠️ = needs Flask/bot work.
The marker applies to the web phases only; Phases 13–17 are bot-side by definition.

Phase numbering continues [WEB_DASHBOARD_PLAN.md](WEB_DASHBOARD_PLAN.md) §6, which ends at
Phase 7 (hardening).

---

## Where things stand

**Sections A–F (appendix) are the original API-surfacing backlog and are shipped**, apart
from three backend items. That work was about rendering data the API already returned.
What remains is a different class of problem: controls that do not work, actions that give
no feedback, machine values shown raw, and a layout that only really works at phone width.

**Phases 13–17 are new**, and cover the half of the project this document has never
described: the bot. They were written after an audit of [`zephyr/`](../zephyr) against the
running code, and every defect in Phase 13 was confirmed in the source rather than inferred.

| Phase | Theme | Status |
|---|---|---|
| A–F | Surface unused API data, code-splitting, PWA, a11y basics | Shipped; **A3** with 9.3, **F4** with Phase 11; only **E5** open |
| **8** | **UX correctness — dead and misbehaving controls** | **Shipped** |
| **9** | **Feedback layer and display vocabulary** | **Shipped** (with **A3**) |
| **10** | **Layout, density and loading states** | **Shipped** |
| **11** | **Navigation and information architecture** | **Shipped** (with **F4**) |
| **12** | **Public site layer** | **Shipped** |
| **13** | **Bot correctness and observability** | **Shipped** |
| **14** | **Bot functionality gaps** | **Shipped** |
| **15** | **New bot features** | ✅ **shipped** (15.1–15.8) |
| **16** | **Discord-side presentation** | ✅ **shipped** (16.1–16.3) |
| **17** | **Code quality and infrastructure** | **17.2–17.4 shipped**, 17.1 partly (engine split; command surface not) |

**Phases 8 to 14 are shipped**, apart from Phase 15's remaining seven features. Next are those,
then Phase 16 (the embed factory, deliberately after 15 so it migrates ~120 sites once) and
Phase 17.

One note carried out of Phase 8 for the phases that follow: the frontend suite now has a
jsdom baseline and a route harness (`test/setup.ts`, `test/helpers.tsx`), so a route-level spec
is a few lines rather than a morning. Every Phase 8 spec was checked by reintroducing the
defect and confirming the spec fails; two did not at first, and both near-misses were
caused by the same thing — asserting on a substring or on a batched render rather than on
the exact requests made. Falsify new specs the same way.

### Baseline, as audited

What already works, so nothing here is spent re-checking it: **553 Python tests pass**, and
CI covers the backend suite, frontend lint / typecheck / test / build, and a Docker build.
The security posture of [`website/`](../website) — CSP, session handling, CSRF, cache
headers — is in good shape and no item below touches it, apart from the two unguarded
surfaces already recorded as **12.5** (soft 404s) and **12.6** (rate limiting on `/player`
only). Both were re-confirmed during the bot audit.

---

# Phase 8 — UX correctness

Dead controls and controls that behave wrongly. None of these need a design decision; each
has one correct answer. All six are S-sized and concentrated on `/weather` and
`/g/:id/music`.

### 8.1 — The queue's "Play" button does nothing · S · ✅ · **DONE**

[`routes/GuildMusic.tsx`](../website/frontend/src/routes/GuildMusic.tsx) — `QueueRow`
renders `<PressableButton className="small soft">Play</PressableButton>` with **no
`onClick`**. Every row in the live queue carries a dead button.

**Fix:** either wire it to `run('play', { query: track.url ?? track.title, mode: 'next' })`
— jump-to-this-track expressed with the endpoints that exist — or delete it. Do not leave a
third state. If wired, relabel it "Play next" so it does not promise an absolute jump the
bridge cannot perform.

**Done when:** no button in the queue row is inert, and its label matches its effect.

### 8.2 — Effects sliders fire one mutation per drag step · S · ✅ · **DONE**

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

### 8.3 — Geocode results show "No matching places" while still loading · S · ✅ · **DONE**

[`routes/Weather.tsx`](../website/frontend/src/routes/Weather.tsx) — the results ternary
tests `places.data?.results?.length`, which is falsy during the fetch, so **every keystroke
flashes a false "No matching places"**. The same query also fires once per character typed.

**Fix:** branch on `places.isPending` before the empty case, and debounce the input by
~250ms — key the query on the debounced value, not the raw one, or react-query still sees a
new key per character.

**Done when:** typing a city goes loading → results and never shows a false empty state,
and the network panel shows roughly one request per pause rather than per keystroke.

### 8.4 — "Use my location" fails silently · S · ✅ · **DONE**

[`routes/Weather.tsx`](../website/frontend/src/routes/Weather.tsx) — `locate()` passes
`() => undefined` as the geolocation error callback. Deny the browser permission, or let it
time out, and the button is permanently inert with no explanation.

**Fix:** hold the error in state and surface it through the 9.1 toast host (an `ErrorNote`
until that lands), distinguishing *denied* ("Location permission is off for this site")
from *unavailable* ("Could not get a fix — search for your city instead"). Pass
`{ timeout: 10000 }` as well; the default is no timeout at all.

**Done when:** denying permission produces a visible, actionable message.

### 8.5 — The weather page has no error state · S · ✅ · **DONE**

[`routes/Weather.tsx`](../website/frontend/src/routes/Weather.tsx) — the forecast block is
gated on `place && weather.data` and `weather.isError` is never read, so a failed
`/weather` call renders **nothing at all** below the search field. Every authenticated
screen handles this with `ErrorNote`; the most-visited public page does not.

**Fix:** add the `weather.isError` branch with
`<ErrorNote error={weather.error} onRetry={() => weather.refetch()} />`, matching
[`routes/GuildMusic.tsx`](../website/frontend/src/routes/GuildMusic.tsx). The geocode query
needs the same treatment.

**Done when:** a forced 500 on `/weather` shows a message and a working Try again.

### 8.6 — Saved weather places cannot be removed · S · ✅ · **DONE**

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

### 9.1 — There is no transient feedback anywhere · M · ✅ · **DONE**

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

### 9.2 — Weather numbers carry no units · S · ✅ · **DONE**

[`routes/Weather.tsx`](../website/frontend/src/routes/Weather.tsx) renders `Wind {…}`,
`Rain {…}` and `wind {day.wind_speed_max}` as bare numbers. C8 added a metric/imperial
preference, so "Wind 12" is now genuinely ambiguous — km/h or mph, mm or inches.

**Fix:** a `formatUnit` helper in `lib/` keyed off `preferences.units`, used for wind,
precipitation and every other dimensioned value. Temperatures already carry `°`; add the
scale to the current-conditions card so the page declares its system once.

**Done when:** no dimensioned number renders without its unit in either system.

### 9.3 — Raw Discord snowflakes on the server overview · M · ⚠️ · **DONE**

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

### 9.4 — Audio effect names are unmapped identifiers · S · ✅ · **DONE**

[`routes/GuildMusic.tsx`](../website/frontend/src/routes/GuildMusic.tsx) labels effects with
`effect.replace('_', ' ')`, so the UI reads "sixteen d" and "slownrev".

**Fix:** an explicit label map — `sixteen_d` → "16D Audio", `slownrev` → "Slowed + Reverb",
and so on — plus a one-line `detail` per row saying what the effect does. These are
unguessable from the name.

**Done when:** no effect row shows a snake_case identifier.

### 9.5 — Now-playing art placeholder prints its own name · S · ✅ · **DONE**

[`routes/GuildMusic.tsx`](../website/frontend/src/routes/GuildMusic.tsx) — the fallback is
`<span className="art-placeholder" aria-hidden>track art</span>`, which renders the literal
words "track art" where the thumbnail should be. Replace with an icon from
[`components/icons.tsx`](../website/frontend/src/components/icons.tsx) on a `--surface-2`
tile.

### 9.6 — The hourly strip uses text emoji · S · ✅ · **DONE**

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

### 10.1 — List rows are unreadable at desktop width · M · ✅ · **DONE**

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

### 10.2 — Music is one flat scroll of nine control groups · M · ✅ · **DONE**

[`routes/GuildMusic.tsx`](../website/frontend/src/routes/GuildMusic.tsx) stacks search → now
playing → transport → loop/volume/autoplay → eight effects → queue → playlists in one
column at every width. On desktop that is a very long scroll with the queue permanently
below the fold while half the viewport sits empty.

**Fix:** two columns at ≥1200px — player, transport and effects left; search, queue and
playlists right. The 860px breakpoint stays as it is; this is a third, wider arrangement,
not a change to the existing one. Reuse the `GuildShell` grid rather than inventing a
second layout mechanism.

**Done when:** at 1440px the queue is visible without scrolling past the player.

### 10.3 — Skeletons still have one generic shape · S · ✅ · **DONE**

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

### 11.1 — `/commands` is unreachable · S · ✅ · **DONE**

The command reference — 73 commands, shipped as **C10** — has **no entry point in the UI**.
It is absent from `TABS` in [`components/TabBar.tsx`](../website/frontend/src/components/TabBar.tsx),
from the nav in [`components/AppShell.tsx`](../website/frontend/src/components/AppShell.tsx),
and from `FEATURES` in [`routes/Home.tsx`](../website/frontend/src/routes/Home.tsx) — which
instead advertises the internal design-system page as one of three cards. Only the ⌘K
palette can reach it.

**Fix:** add it to the top-bar nav and the home feature grid. Taking the `/kitchen-sink`
slot is the natural swap — see 11.4.

### 11.2 — One destination, two names · S · ✅ · **DONE**

`/settings` is labelled **"Appearance"** in the top bar and **"System"** in the tab bar.
Pick one. "Appearance" describes the contents; "System" does not.

### 11.3 — Back links on top-level destinations · S · ✅ · **DONE**

`/weather` and `/commands` are primary tab destinations, and both end with
`<BackLink to="/">Back home</BackLink>` while the tab bar is showing Home. A back
affordance on a root destination reads as a bug. Keep `BackLink` on guild sub-pages, where
it is correct.

### 11.4 — The design-system page is a public headline feature · S · ✅ · **DONE**

[`routes/Home.tsx`](../website/frontend/src/routes/Home.tsx) gives `/kitchen-sink` one of
three cards on the landing page, selling an internal review surface to prospective users.
The route stays — it is the design contract — but not as a third of the hero grid. Replace
that card with Commands or Music and demote `/kitchen-sink` to a footer or `/settings` link.

### 11.5 — `/commands` needs in-page navigation · M · ✅ · **DONE**

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

### 12.1 — A visitor cannot add the bot · S · ⚠️ · **DONE**

`invite_url` is returned only by `GET /api/v1/me` and rendered only in
[`routes/Guilds.tsx`](../website/frontend/src/routes/Guilds.tsx) — behind auth. Someone
landing on `/` is offered "Check the weather" and "Open dashboard" and no way to install
Zephyr. For a bot site that is the primary conversion action.

**Fix:** include the invite URL in the unauthenticated `GET /api/v1/status` response,
derived from `DISCORD_CLIENT_ID` + `DISCORD_INVITE_PERMISSIONS` exactly as
[`website/api/me.py`](../website/api/me.py) does, and make "Add Zephyr to Discord" the
primary hero button.

### 12.2 — No Privacy Policy or Terms · M · ⚠️ · **DONE**

Neither exists anywhere in the repo. Discord requires both URLs for app verification, and
the service genuinely processes personal data: Discord user ids, OAuth tokens in Redis,
per-channel AI conversation memory, and audit rows carrying `actor_id`.

**Fix:** `/privacy` and `/terms` routes plus a documented data-deletion path — ideally
self-service, at minimum a stated contact route. The AI memory purge already implements
deletion for the largest data category, so describe it. Link both from the footer (12.4)
and register them on the Discord application.

### 12.3 — No link previews, and one title for every route · S · ✅ · **DONE**

[`website/frontend/index.html`](../website/frontend/index.html) has no Open Graph or Twitter
card tags, so a link to the site pasted **into Discord** renders as a bare URL. For a
Discord bot that is the highest-leverage missing markup on the site.

**Fix:** `og:title`/`description`/`image`/`url` and `twitter:card=summary_large_image`, with
a 1200×630 image generated from the design tokens the way
[`scripts/generate_icons.py`](../scripts/generate_icons.py) generates the PWA icons.

Separately, every route shares one static `<title>` ("Zephyr Weather"), so browser history
and tabs cannot tell `/commands` from `/weather`. Add per-route title, description and
canonical.

### 12.4 — No footer · S · ✅ · **DONE**

No support-server link, no repository link, no legal links, no copyright. This is where most
of the "is this a real product" signal lives, and where 12.2's pages get linked from.

### 12.5 — Crawlers and soft 404s · M · ✅ · **DONE**

No `robots.txt` or `sitemap.xml` in
[`website/frontend/public/`](../website/frontend/public/) — add both, disallowing `/g/*`,
`/login` and `/kitchen-sink`. The SPA is lazy-loaded with no prerendering, so a crawler gets
an empty shell; build-time prerendering of the four public routes is the right scale of fix,
not SSR.

Separately, [`website/spa.py`](../website/spa.py) serves `index.html` with **HTTP 200** for
every unknown path, so `/nonsense` is an indexable soft 404 that renders `NotFound`. Return
404 for paths outside the known route table.

### 12.6 — Public endpoints are unguarded · M · ⚠️ · **DONE**

`rate_limit()` in [`website/api/guard.py`](../website/api/guard.py) is used by
[`website/api/player.py`](../website/api/player.py) only. On a public origin `/weather` and
`/geocode` (which spend an upstream Open-Meteo quota), `/ai/*` (a free-tier Gemini key) and
`/auth/login` are all unlimited.

**Fix:** per-IP limits on the public read endpoints and a tighter one on `/auth/login`.
`TRUST_PROXY` and `ProxyFix` are already wired, so the client address is trustworthy behind
Render.

### 12.7 — Operational blind spots · M · ⚠️ · **DONE**

No error tracking (a production 500 is invisible), no analytics (no way to know whether
anyone visits), no uptime monitoring. Render's free web tier also spins down, so a cold
visitor sees the home status pill read "Bot offline" while the service wakes — the pill
should distinguish *waking* from *offline*.

If analytics are added, choose a cookieless option so no consent banner is needed, and note
that `connect-src 'self'` in [`website/security.py`](../website/security.py) must then be
widened deliberately.

---

# Phase 13 — Bot correctness and observability

The bot side has never been in this document. These six are the equivalent of Phase 8:
defects a user or an operator hits, each with one correct answer. **13.1 and 13.2 are the
two highest-value items in the whole backlog** — until they land, every other bot defect is
diagnosed by guesswork.

### 13.1 — Slash commands have no error handler at all · M · **DONE**

[`zephyr/client.py`](../zephyr/client.py) registers no `on_app_command_error` and no
`tree.on_error`. The only handler in the package is `MusicCog.cog_command_error`
([`zephyr/cogs/music.py`](../zephyr/cogs/music.py) line 1510) — which is the
**prefix-command** hook taking a `commands.Context`, not the app-command one, so it never
fires for any of the 75 slash commands.

An unhandled exception inside a slash command therefore produces "The application did not
respond" or a silent failure, with nothing written anywhere. Every cog is affected.

**Fix:** one `tree.error` handler on `ZephyrBot`, plus `on_command_error` for the prefix
surface. Branch on the cases that are the user's fault and answer them plainly —
`CommandOnCooldown` (say how long), `MissingPermissions`, `CheckFailure`, `TransformerError`
— and treat everything else as a bug: log the traceback via 13.2 and reply with a short
apology carrying a correlation id. Check `interaction.response.is_done()` before replying,
or the handler raises inside itself on any command that already deferred.

**Done when:** a deliberately raised exception in any slash command produces a visible
message to the user and a full traceback in the log, and no command can fail silently.

### 13.2 — Nothing in the bot logs, and no traceback is ever kept · M · **DONE**

`logging` is not imported anywhere in [`zephyr/`](../zephyr). There are **65 `print()`
calls**, and the **68** `except Exception as e` blocks almost all print `str(e)` — so the
stack is discarded at the point of failure. What reaches the operator is a bare sentence
with no timestamp, no level, no module and no line number.

This is the root cause of the project's debuggability problem: 13.1 has nowhere to send a
traceback, 12.7 has nothing to ship to error tracking, and a Render log stream cannot be
filtered or levelled.

**Fix:** a `zephyr/core/logging.py` configuring the root logger once (level from `LOG_LEVEL`,
plain format locally and JSON in the cloud), called from [`run_bot.py`](../run_bot.py) and
[`run_web.py`](../run_web.py). Replace `print()` with a module-level
`log = logging.getLogger(__name__)`, and every `print(f"...{e}")` with `log.exception(...)`,
which captures the stack automatically inside an `except`. Keep the deliberate startup
banner in `setup_hook` as prints; that is UI, not logging.

**Done when:** no `print()` remains outside the startup banner, and an exception raised in a
cog appears in the log with its full traceback.

### 13.3 — `/language` changes TTS for every server at once · S · **DONE**

[`zephyr/cogs/voice_tts.py`](../zephyr/cogs/voice_tts.py) stores the language as
`self.tts_language` on the cog instance (line 21), read at line 54 and written at line 71.
The cog is a singleton, so one user running `/language fr` switches the voice for **every
guild the bot is in**, silently, until the next restart.

**Fix:** move it into guild settings alongside the other per-guild values in
[`zephyr/db/guild_settings.py`](../zephyr/db/guild_settings.py), keyed by guild id with a
DM fallback keyed by user. The settings loader and the dashboard settings screen already
have the shape for this.

**Done when:** two servers can hold two different TTS languages at once, and the choice
survives a restart.

### 13.4 — `command_prefix="/"` collides with the slash surface · S · **DONE**

[`zephyr/client.py`](../zephyr/client.py) line 42 sets `command_prefix="/"`, so **every
message beginning with a slash is also parsed as a prefix command**. A user who mistypes
`/pley` raises `CommandNotFound` on a code path with no handler (13.1), and the 13 real
prefix commands are indistinguishable from the 75 slash commands in the client UI.

Two smaller things in the same constructor:

- Line 44 registers `commands.DefaultHelpCommand` while [`zephyr/cogs/help.py`](../zephyr/cogs/help.py)
  provides the real help surface — two help implementations, one of them unstyled.
- Line 40 requests `discord.Intents.all()`, which includes privileged intents the cogs never
  read and which must each be justified for verification past 100 guilds.

**Fix:** a distinct prefix (`z!`, or the per-guild value from 14.1), `help_command=None`, and
an explicit `Intents` set naming only what is used — `guilds`, `members`, `message_content`,
`voice_states`.

**Done when:** a mistyped slash command is handled by Discord rather than the prefix parser,
`/help` has one implementation, and the intent set is enumerated rather than `.all()`.

### 13.5 — AI quota accounting is per-process and lost on restart · M · **DONE**

The Gemini rate-limit state — `model_request_windows`, `model_token_windows`,
`model_daily_requests`, `model_cooldowns`, `model_usage_totals` — is five module-level dicts
in [`zephyr/services/gemini.py`](../zephyr/services/gemini.py) (lines 110–117). The
image-generation cooldowns and cache are four more in
[`zephyr/cogs/chat.py`](../zephyr/cogs/chat.py) (lines 51–54).

Consequences, all real on the free-tier key this project runs on:

- A restart hands out a **fresh daily allowance the key does not have**, so the next burst
  hits Google's 429 rather than the local limiter that exists to prevent exactly that.
- The bot process and the web process each keep their own copy, so `/token` in Discord and
  the AI panel in the dashboard report different numbers for the same key.
- B4's `cooldown_until` banner is therefore advisory at best.

**Fix:** move the windows and daily counters into Redis — already wired through
[`zephyr/services/redis_client.py`](../zephyr/services/redis_client.py) and already the
transport for the bridge — keeping the in-memory dicts as the fallback when `REDIS_URL` is
unset. Key daily counts on the UTC date so they expire themselves.

**Done when:** restarting the bot does not reset the daily count, and `/token` and the
dashboard agree.

### 13.6 — The bot does not react to an emptying voice channel · S · **DONE**

There is no `on_voice_state_update` listener in
[`zephyr/cogs/music.py`](../zephyr/cogs/music.py). When the last human leaves, playback
continues to an empty channel until the idle timer eventually fires, burning bandwidth and
an FFmpeg process for as long as the queue lasts.

**Fix:** listen for the event; when the bot is alone, pause and start a short grace timer
(~60s) before disconnecting, cancelling it if someone rejoins. Announce the disconnect in the
notify channel the way the idle timeout already does. Handle the inverse too — being
force-moved or disconnected by a moderator should tear the `VoiceState` down rather than
leave a stale entry in `self.voice_states`.

**Done when:** the bot leaves an empty channel within a minute, resumes cleanly if someone
returns first, and a server-side disconnect leaves no orphaned voice state.

---

# Phase 14 — Bot functionality gaps

Capability the bot is missing relative to what its own dashboard already does.

### 14.1 — The prefix is hardcoded · M · **DONE**

Follows 13.4. Once the prefix is no longer `/`, it should be per-guild and editable from
[`routes/GuildSettings.tsx`](../website/frontend/src/routes/GuildSettings.tsx), which already
edits every other guild-scoped value. `command_prefix` accepts a callable, so this is a
settings lookup rather than a restructure.

### 14.2 — No autocomplete on any command · M · **DONE**

`/play` takes a free-text string; `/setlocation` and the weather commands take raw city
names. `app_commands.autocomplete` would surface live search results, the user's saved
playlists ([`zephyr/db/playlists.py`](../zephyr/db/playlists.py)) and their recent locations
directly in the Discord client. This is the largest single ergonomics gain available on the
Discord side and every data source for it already exists.

Autocomplete callbacks must answer within 3 seconds, so cache the geocode and search lookups
rather than calling upstream per keystroke — the same mistake 8.3 fixes on the web.

### 14.3 — The queue is richer on the web than in Discord · M · **DONE**

The dashboard can drag-reorder, clear, jump and remove (C1–C5); the bot has bridge handlers
for all of it — `_bridge_move`, `_bridge_jump`, `_bridge_remove`, `_bridge_clear` in
[`zephyr/cogs/music.py`](../zephyr/cogs/music.py) — but no paginated queue view exposing
them to Discord users. [`zephyr/utils/pagination.py`](../zephyr/utils/pagination.py) already
exists.

**Done when:** the queue can be reordered and trimmed from Discord without opening the site.

### 14.4 — AI chat gaps · M · **DONE**

Four separate items across [`zephyr/services/gemini.py`](../zephyr/services/gemini.py) and
[`zephyr/cogs/chat.py`](../zephyr/cogs/chat.py):

- **No per-channel opt-out.** The mention/reply handler in
  [`zephyr/client.py`](../zephyr/client.py) responds anywhere the bot can read. An allow or
  deny list of channels belongs in guild settings.
- **No per-user token budget.** Limits are per model, not per user, so one person can consume
  a guild's whole daily allowance.
- **Responses arrive as one block.** Gemini streams; editing a message progressively would
  remove the dead air on long answers.
- **Attachments are ignored.** The models in use accept images; a replied-to image should be
  passed as context.

Treat these as four independent paths rather than one tool-using agent: on the free-tier key
the 2.5 models cannot combine most tools in a single request.

### 14.5 — Weather subscriptions cannot be paused or tested · S · **DONE**

[`zephyr/db/weather_subs.py`](../zephyr/db/weather_subs.py) models enable/disable but there
is no snooze (mute until a date) and no "run this one now", so a user setting up a digest
cannot see what it will look like without waiting for the schedule. Both are small additions
to [`zephyr/cogs/weather_alerts.py`](../zephyr/cogs/weather_alerts.py) and
[`website/api/weather_subs.py`](../website/api/weather_subs.py).

### 14.6 — Single process, no sharding · L · **DONE**

`ZephyrBot` is a plain `commands.Bot`. Discord requires sharding past ~2500 guilds and
`AutoShardedBot` is close to a drop-in — but the module-level state in 13.5 and
`self.voice_states` both assume one process, so 13.5 is a prerequisite. Not urgent; recorded
so the constraint is visible before it becomes urgent.

---

# Phase 15 — New bot features

Unlike Phases 8–14, these are additions rather than corrections, so each needs a product
decision before it needs an implementation. Ordered by how much existing machinery they
reuse — the top three are mostly wiring.

| # | Feature | What it reuses | Effort |
|---|---|---|---|
| 15.1 ✅ | **`/remindme` and a reminder list** | The scheduler and durable job loop in [`zephyr/cogs/weather_alerts.py`](../zephyr/cogs/weather_alerts.py), plus a table alongside [`weather_subs`](../zephyr/db/weather_subs.py) | M |
| 15.2 ✅ | **`/export-my-data`, `/delete-my-data`** | The AI memory purge already implements deletion for the largest data category; this is the self-service path 12.2 needs | M |
| 15.3 ✅ | **Moderation commands + modlog** | [`zephyr/db/audit.py`](../zephyr/db/audit.py) and the dashboard audit screen, which would gain real content | L |
| 15.4 ✅ | **Skip-vote, DJ-only lock, 24/7 mode** | DJ roles are already modelled (`dj_role_id`, `reload_dj_roles`) | M |
| 15.5 ✅ | **Welcome / farewell messages** | `on_guild_join` exists; needs per-guild config the dashboard can edit | M |
| 15.6 ✅ | **Starboard / highlights** | `guild_settings` plus one reaction listener | M |
| 15.7 ✅ | **Activity stats or leveling** | Feeds 12.7's analytics and the still-empty dashboard activity feed (F5) | L |
| 15.8 ✅ | **Tags / custom responses** | Natural pairing with the existing persona editor (C6) | M |

**15.2 is the only item here that unblocks something else** — schedule it with 12.2, since
Discord verification wants a stated deletion path and this is the self-service version of it.

---

# Phase 16 — Discord-side presentation

> **Shipped.** The counts in this section were an undercount: `weather.py`
> imports `Embed` bare, so its whole slash-command half was invisible to a
> search for `discord.Embed(` -- and that half was choosing *raw hex* colours.
> The real totals were **130 construction sites** and **seventeen** distinct
> colours, now six roles from `zephyr/utils/embeds.py`. The rules are written
> down in [BOT_OUTPUT.md](BOT_OUTPUT.md) and guarded by
> `tests/test_embed_style.py`.

The web has [DESIGN.md](DESIGN.md). The bot's output has no equivalent, and it shows.

### 16.1 ✅ — Every cog builds embeds its own way · M

Weather, music and AI each pick their own colours, footer text and timestamp conventions, so
the bot reads as three bots sharing an avatar.

**Fix:** one embed factory in [`zephyr/utils/`](../zephyr/utils) carrying the accent palette,
a consistent footer and the bot icon; route every `discord.Embed(...)` construction through
it. This is also the natural home for the error embed 13.1 needs.

**Done when:** no cog constructs `discord.Embed` directly.

### 16.2 ✅ — Ephemeral responses are inconsistent · S

Errors and settings confirmations are ephemeral in some cogs and public in others, with no
stated rule. Pick one — *errors and personal settings ephemeral, shared state public* — and
apply it throughout. Today a failed `/play` can spam a busy channel while a successful
settings change disappears from view.

### 16.3 ✅ — The command list exists twice · M

[`zephyr/utils/help_data.py`](../zephyr/utils/help_data.py) (257 lines) and the web command
reference behind `GET /commands` are two hand-maintained descriptions of the same 75 slash
and 13 prefix commands. They will drift, and the README's counts are a third copy.

**Fix:** derive one from the other — walk `bot.tree` at startup and publish the result over
the bridge, leaving `help_data.py` as the source only for prose that cannot be derived
(categories, examples). Add a test asserting the counts match.

**Done when:** adding a command updates `/help`, `/commands` and the README count without a
second edit.

---

# Phase 17 — Code quality and infrastructure

### 17.1 ◐ — Two files hold a third of the codebase · L

[`zephyr/cogs/music.py`](../zephyr/cogs/music.py) is **2,589 lines** and holds `Track`,
`YTDLSource`, `SongQueue`, `VoiceState`, `NowPlayingView`, twenty `_bridge_*` handlers and
the entire command surface. [`zephyr/cogs/weather.py`](../zephyr/cogs/weather.py) is
**1,003**. Together they are a third of the 11.4k-line Python codebase, and they are the two
files most often edited.

**Fix:** extract music into `zephyr/music/{sources,queue,state,views,bridge}.py` with the cog
reduced to command definitions, then weather along the same lines. Do it **after** Phase 13 —
the error-handler and logging changes touch these files, and rebasing a 2,600-line move over
them is avoidable pain.

**Done when:** no module in `zephyr/` exceeds ~600 lines and the test suite is unchanged.

**Partly shipped, and the unshipped half is named here rather than quietly dropped.**

Done: the engine is out. `zephyr/music/` holds `common.py` (207), `queue.py` (63),
`sources.py` (403), `state.py` (476) and `views.py` (280) — every one inside the 600-line
target — and the suite is unchanged: 1,358 tests, no spec edited except one stale path
string. `zephyr/cogs/music/__init__.py` re-exports the lot, so all twenty-odd
`from zephyr.cogs.music import …` imports still resolve, and `discord` itself is re-exported
because three tests patch `zephyr.cogs.music.discord.FFmpegPCMAudio`.

The seam was already in the code: `VoiceState`'s own docstring says it takes *callbacks*
rather than a back reference to the cog, "so this class still knows nothing about Redis and
nothing about the button view's permission model". The split follows that boundary exactly.

**Not done: the command surface.** `zephyr/cogs/music/__init__.py` is 1,933 lines — the
~90 command methods, the listeners and the bridge handlers. Getting *that* under 600 means
splitting the cog class across mixins. It would work (`CogMeta` collects app commands across
the MRO), and it is the highest-risk change left in this document for a purely
readability-shaped gain: ninety commands whose registration, autocompletes and permission
decorators all depend on being collected correctly. **`zephyr/cogs/weather.py` (1,042) is
untouched for the same reason.** Both are capped by
[`tests/test_music_package.py`](../tests/test_music_package.py) at their current size, so
neither can grow without somebody deciding to raise the ceiling.

Two things about the split are worth knowing before moving anything else.
`EMPTY_CHANNEL_GRACE_SECONDS` and `list_playlists` stay defined in the cog module because a
module-level name is read through its *own* module's globals — patching a re-export would
apply to a name nothing reads, and the test would pass while testing nothing. And the engine
must never import `zephyr.cogs`; a guard asserts it, because that direction would make the
two one module again with extra files.

### 17.2 ✅ — Error tracking and uptime, bot side · M

The bot half of 12.7. Once 13.2 exists, attaching Sentry (or equivalent) to the logging
config covers both processes at once, and 13.1's handler is the natural capture point. A
production exception in a cog is currently invisible unless someone happens to be reading the
Render log stream at the moment it happens.

### 17.3 — Two schema paths are active at once · S · **DONE**

[`alembic.ini`](../alembic.ini) with four migrations in
[`zephyr/db/migrations/versions/`](../zephyr/db/migrations/versions), **and** `DB_AUTO_CREATE`
defaulting to `1` in [`zephyr/config.py`](../zephyr/config.py), which create-alls the
metadata. Locally that is convenient; in a deployed environment it means a schema can appear
without a migration and then diverge from one.

**Fix:** keep auto-create for SQLite development only and make migrations the sole path
whenever `DATABASE_URL` is set. State which is which in [DEPLOYMENT.md](DEPLOYMENT.md).

**Shipped** with the Phase 13–15 schema batch, because six new-table revisions are exactly
the case where the double path bites and it had to be fixed before them, not after.
`should_auto_create(url)` in [`zephyr/db/engine.py`](../zephyr/db/engine.py) decides per URL;
`DB_AUTO_CREATE` became tri-state and remains an override in both directions. The recovery
for a database already built by `create_all` — a one-time `alembic stamp head` — is in
[DEPLOYMENT.md](DEPLOYMENT.md), since no test can detect that state.

### 17.4 ✅ — No end-to-end test on the frontend · M

The frontend suite covers `lib/` helpers and the `ios/` primitives. Every defect in Phase 8
is a *wiring* defect — a button with no `onClick`, a query keyed on the wrong value, an error
branch never read — which unit tests on primitives cannot see. One Playwright pass over login
→ guild → music → queue a track, plus the public weather search, would catch that entire
class.

**Fix:** add Playwright to [`website/frontend`](../website/frontend) and a fourth CI job in
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml), run against the Flask app with a
stubbed bridge so no live bot is needed.

**Done when:** a button rendered without a handler fails CI.

**Shipped.** Twenty-three Playwright specs in [`website/frontend/e2e/`](../website/frontend/e2e)
over the real Flask app serving the real built bundle, plus a fourth CI job. Writing them
found four real things — a service worker swallowing every route stub, a five-migrations-stale
development database, a hand-written fixture that diverged from the response shape, and two
wrong assumptions in the first draft of the specs themselves. The job is the only one with a
service container, which is an argued exception to `conftest.py`'s no-services rule and is
recorded there.

---

## Suggested order

The two tracks are independent — nothing in Phases 13–17 blocks Phases 8–12 or the
reverse — so this is one list rather than two, ordered by value per hour.

1. **Phase 8** — the six web correctness bugs. All S, all unambiguous.
2. **13.2 then 13.1** — logging, then the slash-command error handler. In that order: the
   handler needs somewhere to put a traceback. Everything else on the bot side is easier to
   diagnose once these exist, and 17.2 becomes a small wiring job rather than a project.
3. **9.1** — the toast host. Everything after it gets to use it, and 8.4 wants it.
4. **13.3, 13.4, 13.6** — three S-sized bot fixes; 13.3 is a live cross-guild bug.
5. **9.2–9.6** — units and vocabulary; the cheapest visible quality gain in the app.
6. **10.1** — the list measure cap. One rule, fixes every screen on desktop.
7. **11.1–11.4** — four S-sized IA fixes.
8. **12.1–12.4 with 15.2** — the public layer. 12.2 blocks Discord verification and 15.2 is
   the deletion path it has to describe, so do them together.
9. **13.5** — durable AI quota state. Do it before 14.6 ever becomes relevant.
10. **17.1** — split `music.py` and `weather.py`, once Phase 13 has stopped editing them.
11. **14.2, 16.1, 16.3** — autocomplete and the presentation cleanups; the visible half of
    the bot's polish.
12. **10.2, 10.3, 11.5, 12.5–12.7, 14.1, 14.3–14.5, 16.2, 17.3, 17.4** — by appetite.
13. **Phase 15** — new features, once the corrections above are done. Each needs a product
    decision first.
14. ~~**F4**~~ — shipped with Phase 11. **A3** shipped with 9.3.

**E5 (the SSE player stream) is deliberately deferred to its own branch**, and the
blocker is deployment rather than code. `Procfile` runs gunicorn with **default sync
workers**, so one dashboard tab holding an `EventSource` occupies a worker for the life
of the connection and a handful of tabs starve the app. Doing it honestly means
`--worker-class gthread --threads 8` plus a hard server-side stream lifetime (~60s;
`EventSource` reconnects natively) and `X-Accel-Buffering: no` — a deployment change with
its own risk budget. Two notes for whoever takes it: `connect-src 'self'` already permits
it, and an `EventSource` is not a navigation so the service worker's
`navigateFallbackDenylist` is irrelevant. Phase 9 also removed the *urgency* — the
"confirmation arrives whenever the 3s poll lands" symptom was exactly what the toast host
fixed, so E5 is now a latency nicety rather than a correctness fix.

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
