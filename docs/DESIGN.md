# Zephyr Web — Design System ("Warm glass")

The dashboard's look is a token-driven glass system: warm clay and cream, a serif
display face, and frosted surfaces over a drifting aurora. This is the reference for
how it is put together and how to extend it without drifting. The live proof of every
primitive is the **`/kitchen-sink`** route — open it in both appearances before and
after any visual change.

## Principles

- **Glass, surgically.** Frosted surfaces frame content over the aurora backdrop —
  cards, sheets, the top bar, the tab bar, the command palette, the save bar. They are
  never nested glass-in-glass and never used on dense list rows (those are opaque,
  because a 12px label needs solid ground). Glass earns its place; it is not the whole
  chrome.
- **Tokens, not literals.** Colour, shape, motion and elevation live once per theme as
  CSS custom properties in `src/styles/theme.css`. A component never writes a hex value;
  it reads a token, so light and dark stay in lockstep.
- **Serif for display, sans for reading, mono for comparing.** Headings, the wordmark,
  temperatures and stat values are Source Serif 4. Body copy is the system sans, which
  is what gives the UI its iOS feel. Anything the eye scans column-to-column — times,
  volumes, prefixes, audit timestamps — is mono or tabular-figure.
- **Intentional motion.** Springs come from the `motion` library on tap/press; CSS
  transitions carry hover and theme changes. Everything is disabled under
  `prefers-reduced-motion`.

## Theming

- The whole theme is one class on `<html>`: `.dark` present = dark, absent = light.
- `src/lib/theme-context.ts` holds the rule (`resolveTheme`): an explicit stored choice
  (`localStorage['zephyr-theme']`) wins; otherwise follow the OS. `src/lib/theme.tsx`'s
  `ThemeProvider` keeps the class, storage and OS preference in sync; `ThemeToggle`
  flips it.
- **No flash on load:** the inline snippet in `index.html` sets the class before the
  bundle runs, mirroring `resolveTheme`. Keep the two in step if the rule changes.
  (Note: `website/security.py` currently sets `script-src 'self'` with no hash or nonce,
  which blocks that inline snippet in production. Fixing the CSP is a separate change.)
- Per-scheme `theme-color` meta tags keep the mobile browser chrome matching the page.

## Token groups (see `theme.css`)

- **Palette** — `--bg` / `--bg-2`, three `--aurora-*` blobs, `--text` / `--text-muted` /
  `--text-faint`, `--surface-glass` (+ `-border`), `--glass-solid`, `--surface-1..3`,
  `--hairline`, `--accent` (+ `-strong` / `-soft`), `--success`, `--danger` (+ `-soft`),
  `--link`. Defined on `:root` (light) and overridden on `.dark`.
- **Type** — `--font-sans`, `--font-serif`, `--font-mono`.
- **Glass material** — three blur tiers: `--blur-thin | -regular | -thick`, applied by
  **role, not taste**: thin for day cards and notices, regular for the top bar and
  content cards, thick for chrome that floats over content. A sheet reads heavier than a
  card because it is a tier up, not because it invented its own number.
- **Shape** — `--radius-control | -card | -sheet | -pill`.
- **Motion** — `--dur-fast | -dur`, `--ease`.
- **Elevation** — `--shadow-card`, `--shadow-pop`.

### Two palette decisions worth not undoing

- **The third aurora blob stays cool** (`--aurora-3`, a green-grey). Zephyr is a wind
  companion; an all-warm backdrop drops the product's only piece of visual meaning.
- **`--danger` is rose-leaning, not orange.** The accent is already a warm red-orange
  and four screens put a primary and a destructive button in the same row. Danger is
  also an *outline*, never a fill, for the same reason.

## Performance & accessibility budget

- **Glass budget:** `backdrop-filter` is the most expensive paint. Keep glass to the
  surfaces above. Every glass surface carries `data-glass`, and one
  `prefers-reduced-transparency` rule swaps them all to `--glass-solid` — so a new
  frosted component gets the fallback for free instead of being forgotten.
- **Aurora:** three fixed blobs animating only `transform`; blur radii drop below 860px,
  where it is the most expensive thing the phone paints. It stops under
  `prefers-reduced-motion`.
- **Focus:** one `:focus-visible` ring covers every custom control. Controls carry the
  right roles/labels (`aria-pressed` on toggles, segments and checkbox rows,
  `aria-label` on sliders and icon buttons, `aria-invalid` on rejected fields).
- **Icons:** `src/components/icons.tsx`, all drawing in `currentColor` so one glyph
  serves accent, muted and danger contexts. No icon font, no runtime dependency.

## Layout

- `AppShell` renders the sticky glass top bar above every page (wordmark, contextual
  Servers link, command-palette trigger, theme toggle); pages keep their own
  `<main className="app">` beneath it. `.app` is `container-type: inline-size`, so
  headings clamp against the column they occupy rather than the viewport.
- **The breakpoint is 860px**, used consistently for the tab bar, the guild rail, the
  aurora budget and the save bar offset.
- `TabBar` is the phone's primary navigation (Home / Weather / Servers / System) and is
  hidden above the breakpoint. It is sticky, and `#root` is a flex column so it can be
  the last item in normal flow.
- Guild sub-pages wrap their body in `GuildShell`, which lays out `GuildNav` as a sticky
  208px rail at ≥860px and a horizontally scrolling pill row below it. **One nav
  element serves both** — the rail is first in the DOM, so the shell simply stacks.
