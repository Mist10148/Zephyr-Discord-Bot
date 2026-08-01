# Zephyr Web — Screen Inventory & Redesign Brief

Every route in the SPA, what is on it, and the design contract it must keep.
Source of truth: `website/frontend/src/App.tsx` (route table), `src/routes/*` (screens),
`src/components/ios/index.tsx` (primitives), `src/styles/theme.css` (tokens).

**12 screens** — 4 public, 7 authenticated (1 list + 6 per-server), 1 catch-all.
Plus 3 global chrome layers (top bar, command palette, sheets) that render *over* screens.

---

## 0. Global chrome (present on every screen)

### 0.1 `AppShell` — sticky top bar
Wraps every route. Renders above the page's own `<main className="app">`.

| Slot | Content |
| --- | --- |
| Left | Wordmark link to `/` — glyph `❍` (`.brand-mark`) + "Zephyr" (`.brand-name`) |
| Right | "Servers" link → `/g` (**only** when `pathname` starts with `/g`), then `ThemeToggle` |

- Glass surface so the aurora shows through it. Sticky, full-bleed, inner content constrained.
- `ThemeToggle`: single icon button, inline sun/moon SVG (`currentColor`, no icon dep),
  `aria-pressed`, spring `scale: .9` on tap, haptic on press.

### 0.2 `CommandPalette` — ⌘K / Ctrl-K overlay
Mounted on every route **except** `/login`.

- Closed by default; renders `null` until opened. Toggled by ⌘K/Ctrl-K.
- `.palette-backdrop` (click to dismiss) → `.palette` glass panel.
- Contents: search input ("Search commands…"), fuzzy-filtered list (Fuse.js, threshold `.35`,
  keys `name`/`aliases`/`description`), each row = `<b>name</b>` + `<span>description</span>`,
  empty state "No command found."
- Data: `GET /commands`.

### 0.3 `Sheet` — bottom-sheet modal (Radix Dialog)
Used by Weather Alerts and the Playlist panel. `.sheet-overlay` + `.sheet` with a `.grabber` pill.
Uses the **thick** blur tier so it reads heavier than a card.

### 0.4 `GuildShell` + `GuildNav` — per-server section nav
Wraps the body of all six `/g/:guildId/*` screens.

- Sections: **Overview** (`end`), **Music**, **Weather**, **AI**, **Settings**, **Audit**.
- ≥860px: sticky left rail. <860px: horizontally scrolling pill row at the top.
- Active state via `NavLink` → `.nav-link.active`.

---

## 1. `/` — Home (public)

**File:** [Home.tsx](website/frontend/src/routes/Home.tsx) · **Data:** `GET /status`

### Content
1. **Hero** (`.hero`)
   - Mark: `❍` (`.hero-mark`, decorative)
   - `<h1>` **Zephyr**
   - Tagline: *"A weather-first Discord companion — forecasts, music and AI, with a dashboard to match."*
   - **Status pill** (`role="status"`), three states:
     - pending → `Connecting…` (no modifier class)
     - online → `Bot online` (`.ok`)
     - offline → `Bot offline` (`.off`)
   - Actions: primary **"Check the weather"** → `/weather`; secondary **"Open dashboard"** → `/g`
2. **Feature grid** — 3 interactive glass cards (`.glass.glass-interactive.feature-card`), each with
   an `<h2>`, muted body, and an "Open ›" affordance (`.feature-go` + `.chevron`):

| Card | Body |
| --- | --- |
| Weather → `/weather` | Live conditions, a daily forecast, and heat-index advisories for any city. |
| Dashboard → `/g` | Sign in with Discord to manage music, alerts, AI and settings per server. |
| Design system → `/kitchen-sink` | The glass primitives this whole interface is built from. |

### Design notes
The only marketing surface. Hero is the one place a display-scale type ramp is justified.
Status pill is live data, not decoration — its three states must stay visually distinct
(colour alone is insufficient; it currently carries text + an `<i>` dot).

---

## 2. `/weather` — Weather lookup (public)

**File:** [Weather.tsx](website/frontend/src/routes/Weather.tsx) · **Data:** `GET /geocode?q=`, `GET /weather?lat=&lon=`

### Content
1. `LargeTitleHeader` — **"Weather"** / *"Search any city for live conditions and the week ahead."*
2. **Search input** (`.search-input`, `aria-label="Search city"`), default query `Iloilo City`,
   placeholder "Search a city…". Typing clears the selected place.
3. **Results list** (only while no place is picked) — `ListGroup` of `ListRow`s, label `Name, Country`,
   trailing **"Use"** button. Empty/short-query states:
   - `< 2` chars → row "Type at least two letters"
   - no hits → row "No matching places"
4. **Loading** → `Skeleton lines={5}`
5. **Current conditions card** (`GlassSurface.current-weather`)
   - Head: `<h2>` place name (+ country), `.current-desc` description; right side `.current-temp` = big `26°`
   - Meta row: `Feels like N°`, and `Air quality · <band>` when present
6. **7-day grid** (`WidgetGrid`) — one `GlassSurface` per day: `.day-name`, `.stat-value` = `max°` with
   a `.day-low` ` / min°`, and a muted description.
7. Footer link **"Back home"** → `/`

### Design notes
Two densities on one page: a hero-weight current card, then a uniform widget grid.
`.current-temp` and `.stat-value` are the numeric display styles — they should share a ramp.
This is the best candidate screen for weather iconography, which the current design has none of.

---

## 3. `/kitchen-sink` — Design system review page (public)

**File:** [KitchenSink.tsx](website/frontend/src/routes/KitchenSink.tsx) · **Data:** none (local state only)

The visual-regression page: **every primitive on one screen**. Open it in both appearances
before and after any visual change.

### Content, in order
1. `LargeTitleHeader` — "Design system" / *"Every glass primitive, shown in {theme} appearance."*
2. **Buttons** (`.transport` row): `Toggle appearance` (primary, flips the real theme context),
   `Secondary`, `Danger`, `Disabled`
3. **Segmented control** — `Today | Tomorrow | Week`
4. **Widget cards** — `Weather / 26° / Partly cloudy`, `Air quality / Good / AQI 24`
5. **Interactive card** — "Hover me" glass card with the lift + chevron
6. **Inset list** — five rows exercising each trailing slot:
   `Toggle`, `Volume` (Slider), `Stepper`, `Navigable row` (leading avatar + chevron), `Button row`
7. **Loading** — `Skeleton lines={3}`
8. **Feedback** — `CapsuleToast` neutral ("Saved") and error ("Something went wrong.")
9. Footer link "Back home"

### Design notes
**Any new primitive must be added here.** This page is the contract — if a redesign adds a
component and doesn't render it here, the regression surface silently rots.

---

## 4. `/login` — Sign in (public)

**File:** [Login.tsx](website/frontend/src/routes/Login.tsx) · **Data:** `GET /me`

### Content
- While `/me` is pending → `Skeleton lines={4}` (deliberate: prevents a button flash for
  already-signed-in visitors, who are then `<Navigate>`d to `next`).
- `LargeTitleHeader` — **"Sign in"** / *"Manage the Discord servers you already administer."*
- **Error toast** (`CapsuleToast tone="error"`) when `?error=` is present. Eleven mapped messages:

| Code | Message |
| --- | --- |
| `not_configured` | Sign-in is not configured on this server. |
| `access_denied` | You cancelled the Discord sign-in. |
| `oauth_error` | Discord refused the sign-in request. |
| `invalid_request` | That sign-in link was incomplete. Please try again. |
| `state_mismatch` | That sign-in attempt could not be verified. Please try again. |
| `state_expired` | That sign-in link expired. Please try again. |
| `token_exchange_failed` | Discord would not complete the sign-in. Please try again. |
| `insufficient_scope` | Zephyr needs both the identify and servers permissions to continue. |
| `discord_unavailable` | Discord is not responding. Please try again shortly. |
| `discord_rate_limited` | Discord is rate limiting us. Please try again in a minute. |
| `session_unavailable` | The session store is unavailable. Please try again shortly. |
| *(fallback)* | Sign-in failed. Please try again. |

- **Card** (`GlassSurface`): "Sign in with Discord to manage the servers you already administer."
  + muted privacy line *"Zephyr reads your username and your server list. Nothing else, and it
  never posts as you."* + CTA **"Continue with Discord"**.
- Footer link "Back" → `/`

### Design notes
The CTA is a **real `<a className="ios-button primary">`**, not a button — it is a full-page
navigation to Flask. A redesign must keep it an anchor (middle-click, copy-link, keyboard).
The command palette is deliberately **not** mounted here.

---

## 5. `/g` — Your servers (auth)

**File:** [Guilds.tsx](website/frontend/src/routes/Guilds.tsx) · **Data:** `useMe()` (already resolved by `RequireAuth`)

### Content
1. `LargeTitleHeader` — **"Your servers"**
2. **Identity row** — `ListGroup` with one `ListRow`: leading `UserAvatar`, label `global_name ?? username`,
   detail `@username`
3. **Stale notice** (conditional) — glass card: *"Your server list may be out of date. **Refresh it**."*
4. **Server list** — two mutually exclusive states:
   - **Empty:** "No servers yet." + muted *"You can only manage servers where you have the Manage
     Server permission. Invite Zephyr to one, then reload this page."* + **"Add Zephyr to a server"** anchor
   - **Populated:** `ListGroup` of rows → `/g/:id`, leading `GuildIcon`, label = guild name,
     detail = tri-state presence:
     - `null` → "Bot status unknown"
     - `true` → "Zephyr is in this server"
     - `false` → "Zephyr is not in this server yet"
5. **Sign out** (danger button, label swaps to "Signing out…") + `ErrorNote` on failure
6. Footer link "Back" → `/`

### Design notes
Servers without the bot are **listed, not hidden** — the presence detail carries that meaning,
so it must stay legible. Tri-state, not boolean.

---

## 6. `/g/:guildId` — Server overview (auth)

**File:** [GuildOverview.tsx](website/frontend/src/routes/GuildOverview.tsx) · **Data:** `GET /guilds/:id`

### Content
- **Pending:** `Skeleton lines={6}`.
  **Error:** `LargeTitleHeader "No access"` + `ErrorNote` + "All servers" link (deliberately no
  redirect to `/login` — 403 ≠ 401).
1. **Custom header** (`.large-title.guild-head`) — large `GuildIcon`, `<h1>` guild name,
   subtitle "You own this server" / "You manage this server"
2. **Stat widgets** (`WidgetGrid`, 2 cards): **Prefix** → `.stat-value`; **Zephyr** → `In this server` /
   `Not in this server` / `Unknown`
3. **Config summary list** (7 read-only rows): Locale · Timezone · Default volume (`N%`) ·
   DJ role (or "Not set") · Music channels (ids, or "Any channel") · Enabled modules · Your role
4. **Section links** (5 navigable rows):

| Row | Detail |
| --- | --- |
| Music | Now playing, queue and playlists |
| Weather alerts | Daily digests and severe-weather watches |
| AI | Personas, usage, and channel memory |
| Settings | Prefix, DJ role, music channels |
| Audit log | Who changed what, from where |

5. **Caveat card** — muted, conditional: "This server has not been configured yet, so these are
   Zephyr's defaults." and/or "Zephyr is not in this server, so these settings are not doing anything yet."
6. Footer link "All servers"

### Design notes
Three stacked densities (widgets → read-only facts → navigation) with no visual separation
between groups 3 and 4 today. That's the weakest hierarchy in the app — worth fixing.
The caveat card renders even when both conditions are false (empty glass box) — a redesign
should collapse it.

---

## 7. `/g/:guildId/music` — Music (auth)

**File:** [GuildMusic.tsx](website/frontend/src/routes/GuildMusic.tsx) · **Data:** `usePlayer()` (polled snapshot), `POST` actions

### Content
- **Pending:** `Skeleton lines={6}`. **Error:** header "Music" + `ErrorNote` + "Back to the server".
1. `LargeTitleHeader` — **"Music"** / *"Now playing, queue and playlists for this server."*
2. **Idle card** (when `!live`): "Zephyr is not playing anything here." + muted explanation
   mentioning `/play`
3. **Now Playing** (`GlassSurface.now-playing`, when a track exists)
   - `.art` thumbnail (optional), `<h2>` title, muted `uploader • voice channel`
   - `ProgressBar` (`role="progressbar"`, live `aria-valuenow`)
   - `.times` row: elapsed (ticking, client-side) ←→ duration
4. **Transport** (when `live`) — `❙❙ Pause` / `▶︎ Resume`, `⏭ Skip`, `🔀 Shuffle`, `⏹ Stop` (danger).
   All fire haptics (15 for stop, 8 otherwise).
5. **Player controls list** (when `live`)
   - **Loop** → `SegmentedControl` `off | track | queue`
   - **Volume** → `Slider`, detail `N%` (local value wins while dragging)
   - **Autoplay** → `Toggle`, detail "Keep playing a YouTube Mix when the queue runs out"
6. **Errors** — a 409 is a *refusal* (not in voice, no DJ role, nothing playing) shown inline
   with no retry; anything else gets a retry.
7. **Queue** — `<h2>Queue (n)</h2>`; empty → "Nothing queued."; otherwise rows with
   `title` / `uploader • duration` and per-row **Play** + **Remove**. Overflow line:
   "and N more…" when the snapshot is truncated.
8. **`PlaylistPanel`** (see §7b)

### Design notes
Densest screen in the app. The transport uses **text glyphs, not icons** (`❙❙ ▶︎ ⏭ 🔀 ⏹`) —
the single highest-value redesign target. Queue rows carry two buttons each, which is where the
row layout breaks on narrow viewports.

### 7b. `PlaylistPanel` (section within Music)
- `<h2>Playlists</h2>`, skeleton/error states
- Empty: "No playlists yet. Queue something up in Discord and run `/save`, or import one from Spotify."
- Rows: `name` (or `name (shared)`), detail `N tracks • duration`; actions **Queue**, **Edit**
  (own only), **Delete** (own only)
- **"Import from Spotify"** secondary button → **Sheet**: heading, muted explanation about
  title-only import, URL input, **Import** button
- **Edit** → **Sheet** with a drag-and-drop `PlaylistEditor`: `⠿` drag handle per row (keyboard
  sensor bound — reordering works without a pointer), track title + duration or
  "Resolved when it plays", per-row **Remove**, then **Save order** / **Close**.
  Empty warning: "This playlist is now empty. Saving it will leave it empty."

---

## 8. `/g/:guildId/weather-alerts` — Weather alerts (auth)

**File:** [GuildWeatherAlerts.tsx](website/frontend/src/routes/GuildWeatherAlerts.tsx) · **Data:** `GET/POST/PATCH/DELETE /guilds/:id/weather-subs`

### Content
- **Pending:** `Skeleton lines={6}`. **Error:** header + `ErrorNote` + back link.
1. `LargeTitleHeader` — **"Weather alerts"** / *"Daily digests and severe-weather watches posted
   to your channels."*
2. **Empty state** — "No weather is being posted in this server yet." + muted *"A daily digest
   arrives at a time you pick. The two watches stay quiet until there is something worth saying."*
3. **Subscription rows** — label = `kind_label`, detail = `location → #channel at HH:MM (TZ) • paused`.
   Trailing: `Toggle` (enable/pause), **Preview**, **Delete** (danger)
4. **Toast** — "Paused subscriptions keep their settings." (shown when any sub is paused)
5. **"Add a subscription"** primary button
6. **Create Sheet**
   - `<h2>New subscription</h2>`
   - Rows: **What to post** (select: Daily digest / Severe weather watch / Class suspension watch) ·
     **Place** (text, placeholder "Iloilo City") · **Channel** (select of postable channels, degrades
     to a raw id input when the bot is unreachable) · **Time** (only for `daily`, `08:00`) ·
     **Timezone** ("Leave empty to use the place's own", placeholder "Asia/Manila")
   - Contextual help under the form, per kind:
     - daily — "A forecast posted once a day at a time you choose."
     - severe — "Posted only when wind, rain, heat or a storm crosses a threshold."
     - class_suspension — "Posted when the heat index reaches an advisory level. Advisory only —
       always confirm with your school."
   - Fallback note when the bot is unreachable; **Subscribe** / **Cancel**
7. **Preview Sheet**
   - `<h2>Preview — {location}</h2>`, skeleton / error
   - With an alert: title, summary, field rows; plus a muted duplicate-suppression note
   - Without: "Nothing to report right now." + *"This subscription would stay quiet — which is
     what a watch does most of the time."*
   - **Close**

### Design notes
Three trailing controls per row (toggle + 2 buttons) — the worst row-overflow case in the app.
The Create sheet is the only multi-field form; it currently reuses `ListRow` as a form-field
layout, which is a stretch. A redesign should give forms their own field primitive.

---

## 9. `/g/:guildId/ai` — AI (auth)

**File:** [GuildAI.tsx](website/frontend/src/routes/GuildAI.tsx) · **Data:** `/ai/personas`, `/ai/memory`, `/ai/usage` (polled 10s)

### Content
- **Pending:** `Skeleton lines={7}`. **Error:** header "AI" + `ErrorNote`.
1. `LargeTitleHeader` — **"AI"** / *"Personas, quota and channel memory."*
2. Muted privacy line: *"Only messages sent to Zephyr and its replies are retained as channel memory."*
3. **Usage** — one row: label = model name (or "Loading quota"), detail = `N RPM · N TPM · N today`
4. **Personas** — rows: name, detail "Default persona" / "Available"; actions **Default**
   (disabled when already default) and **Delete** (danger)
5. **Add-persona form** — name input (max 64), system-prompt textarea (max 4000),
   **"Add persona"** submit
6. **Channel memory** — rows: `Channel {id}`, detail `N messages · N tokens`; action **Purge** (danger)
7. **Purge confirmation** — inline block: "Delete all Zephyr exchanges saved for channel {id}?
   This cannot be undone." + **Confirm purge** / **Cancel**

### Design notes
The least designed screen: the form uses **bare `<input>`/`<textarea>`** with no `.text-input`
class, and the destructive confirmation is an inline block rather than a `Sheet` like every other
modal flow. Both are inconsistencies a redesign should resolve.

---

## 10. `/g/:guildId/settings` — Settings (auth)

**File:** [GuildSettings.tsx](website/frontend/src/routes/GuildSettings.tsx) · **Data:** `GET/PATCH /guilds/:id/settings`, `useGuildMeta`

### Content
- **Pending:** `Skeleton lines={6}`. **Error:** header + `ErrorNote` + back link.
1. `LargeTitleHeader` — **"Settings"** / *"Prefix, DJ role, music channels and more."*
2. **Defaults notice** (conditional) — muted: "This server has never been configured, so these
   are Zephyr's defaults. Saving stores them."
3. **Settings list**

| Row | Control | Detail |
| --- | --- | --- |
| Prefix | short text | "1–5 characters" → "Not accepted" on `invalid_value` |
| Locale | short text | — |
| Timezone | text | "e.g. Asia/Manila" → "Not an IANA name" on `invalid_value` |
| Default volume | `Slider` | `N%` |
| DJ role | select of roles (degrades to id input) | "Everyone can control the player until one is set" |

4. **Music channels** — `<h2>`, then a checkbox row per channel (`#name`, detail "Zephyr cannot
   post here" when applicable). Bot unreachable → muted card: "…Music commands are allowed in
   every channel while this is unset."
5. **Errors** + **"Saved"** toast (only when clean)
6. **Actions** — **Save changes** (disabled unless dirty; label → "Saving…") and **Discard**

### Design notes
The only dirty-state screen. Field-level validation is currently expressed by **swapping the
row's `detail` text** — no colour, no icon, no focus movement. That's the main accessibility gap
here. The Save/Discard pair also isn't pinned, so on a long list it scrolls out of view.

---

## 11. `/g/:guildId/audit` — Audit log (auth)

**File:** [GuildAudit.tsx](website/frontend/src/routes/GuildAudit.tsx) · **Data:** `GET /guilds/:id/audit` (cursor-paged)

### Content
- **Pending:** `Skeleton lines={6}`. **Error:** header + `ErrorNote` + back link.
1. `LargeTitleHeader` — **"Audit log"** / *"Who changed what in this server, and from where."*
2. **Empty state** — muted: "Nothing has been changed here yet. Settings edits and player actions
   from the dashboard show up here."
3. **Entry rows** — label = humanised action, detail = `{local timestamp} • {Dashboard|Discord}`

| Wire action | Label |
| --- | --- |
| `settings.update` | Updated server settings |
| `ai.persona.create` | Created an AI persona |
| `ai.persona.update` | Edited an AI persona |
| `ai.persona.delete` | Deleted an AI persona |
| `ai.persona.default` | Changed the default persona |
| `ai.memory.purge` | Purged channel memory |
| `player.*` | `Player: {rest}` |

4. **"Load older"** button (label → "Loading…") while `hasNextPage`

### Design notes
Pure chronological list, no grouping, no filtering, no actor shown. Timestamps render in the
viewer's own locale/zone. The most obvious place to add date grouping and a source badge.

---

## 12. `*` — Not found

**File:** [NotFound.tsx](website/frontend/src/routes/NotFound.tsx)

`LargeTitleHeader "Not found"` + "That page does not exist." + "Home" link.
Deliberately not the marketing page — it catches things like `/g/1/musci`.

---

# Design system reference

## Primitives (`src/components/ios/index.tsx`)

| Component | Role |
| --- | --- |
| `GlassSurface` | Frosted card. `interactive` adds a hover lift for link/button cards. |
| `PressableButton` | `primary` / `secondary` / `danger`. Spring `scale .96` tap, `y: -1` hover. **Not polymorphic** — real links are plain `<a className="ios-button">`. |
| `Sheet` | Radix bottom sheet, overlay + grabber. |
| `SegmentedControl` | `role="group"`, `aria-pressed` per segment. |
| `ListGroup` / `ListRow` | Inset list. Row = leading slot + label/detail + trailing children + chevron (when `to`/`onClick`). Renders as `Link`, `button`, or `div`. |
| `Toggle` | Pill switch, `aria-pressed`. |
| `Slider` | `input[type=range]`, `aria-label` + `aria-valuetext`. |
| `Stepper` | `−` / value / `+`. |
| `LargeTitleHeader` | `h1` + optional subtitle. |
| `CapsuleToast` | `neutral` (`role=status`) / `error` (`role=alert`). |
| `Skeleton` | `n` shimmer lines, `aria-busy`. |
| `WidgetGrid` | Responsive card grid. |
| `TabBar`, `DynamicIsland`, `PullToRefresh` | **Defined but unused** — dead primitives. |

## Tokens (`src/styles/theme.css`)

**Shape** `--radius-control 12px` · `--radius-card 22px` · `--radius-sheet 34px` · `--radius-pill 999px`
**Motion** `--dur-fast .14s` · `--dur .22s` · `--ease cubic-bezier(.2,.8,.2,1)`
**Blur** `--blur-thin 12px` · `--blur-regular 20px` · `--blur-thick 34px`

| Token | Light | Dark |
| --- | --- | --- |
| `--bg` | `#e9f0fb` | `#080b16` |
| `--aurora-1` (sky) | `rgba(120,170,245,.55)` | `rgba(49,86,129,.70)` |
| `--aurora-2` (violet) | `rgba(178,150,232,.45)` | `rgba(78,54,90,.62)` |
| `--aurora-3` (teal) | `rgba(120,214,200,.42)` | `rgba(31,91,87,.55)` |
| `--text` | `#0c1526` | `#f4f6fb` |
| `--text-muted` | `#5a6474` | `#9aa4b6` |
| `--text-faint` | `#8a93a3` | `#6a7383` |
| `--surface-glass` | `rgba(255,255,255,.62)` | `rgba(255,255,255,.08)` |
| `--surface-glass-border` | `rgba(255,255,255,.7)` | `rgba(255,255,255,.14)` |
| `--surface-1` | `#ffffff` | `#141826` |
| `--surface-2` | `#eef2f9` | `#1b2030` |
| `--surface-3` | `#e2e8f2` | `#262c3e` |
| `--hairline` | `rgba(12,21,38,.09)` | `rgba(255,255,255,.10)` |
| `--accent` | `#0a84ff` | `#0a84ff` |
| `--accent-strong` | `#0060df` | `#409cff` |
| `--success` | `#12a150` | `#30d158` |
| `--danger` | `#e5484d` | `#ff453a` |
| `--link` | `#0a68d6` | `#8ab4ff` |

Fonts: `-apple-system, BlinkMacSystemFont, Inter, Segoe UI, system-ui` / mono `ui-monospace, SFMono-Regular, SF Mono, Menlo`.

## Rules a redesign must not break

1. **Glass, surgically.** Cards, sheets, top bar, palette only. Never glass-in-glass.
   **Dense list rows stay opaque.**
2. **Tokens, not literals.** No component writes a hex value.
3. **Theme is one class** — `.dark` on `<html>`. `localStorage['zephyr-theme']` wins over OS.
   The pre-paint snippet in `index.html` must stay in step with `resolveTheme` in
   `src/lib/theme-context.ts`, or the page flashes on load.
4. **Motion budget.** `motion` springs on tap/press; CSS transitions for hover/theme.
   Everything off under `prefers-reduced-motion`. The aurora animates `transform` only.
5. **Transparency budget.** Every glass surface must have an opaque fallback under
   `prefers-reduced-transparency`.
6. **Focus.** One `:focus-visible` ring covers every custom control.
7. **`/kitchen-sink` is the contract.** New primitive → new section there.

## Known weak points (redesign targets)

- Transport controls are text glyphs (`❙❙ ▶︎ ⏭ 🔀 ⏹`), not icons — Music screen.
- Rows with 3 trailing controls overflow on phones — Weather alerts, Music queue.
- `GuildAI` uses unstyled `<input>`/`<textarea>` and an inline confirm instead of a `Sheet`.
- `GuildSettings` signals field errors by swapping `detail` text — no colour, icon, or focus move;
  Save/Discard scroll out of view.
- `GuildOverview` renders an empty glass card when neither caveat applies, and has no visual
  separation between the read-only facts list and the navigation list.
- `TabBar`, `DynamicIsland`, `PullToRefresh` are dead code.
- Weather has no iconography at all.
