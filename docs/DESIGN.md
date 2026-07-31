# Zephyr Web — Design System ("Elevated glass")

The dashboard's look is a token-driven glass system. This is the reference for how it
is put together and how to extend it without drifting. The live proof of every
primitive is the **`/kitchen-sink`** route — open it in both appearances before and
after any visual change.

## Principles

- **Glass, surgically.** Frosted surfaces frame content over the aurora backdrop —
  cards, sheets, the top bar, the command palette. They are never nested glass-in-glass
  and never used on dense list rows (those are opaque). This follows 2026's
  "Glassmorphism 2.0": glass earns its place, it is not the whole chrome.
- **Tokens, not literals.** Colour, shape, motion and elevation live once per theme as
  CSS custom properties in `src/styles/theme.css`. A component never writes a hex value;
  it reads a token, so light and dark stay in lockstep.
- **Intentional motion.** Springs come from the `motion` library on tap/press; CSS
  transitions carry hover/theme changes. Everything is disabled under
  `prefers-reduced-motion`.

## Theming

- The whole theme is one class on `<html>`: `.dark` present = dark, absent = light.
- `src/lib/theme-context.ts` holds the rule (`resolveTheme`): an explicit stored choice
  (`localStorage['zephyr-theme']`) wins; otherwise follow the OS. `src/lib/theme.tsx`'s
  `ThemeProvider` keeps the class, storage and OS preference in sync; `ThemeToggle`
  flips it.
- **No flash on load:** the inline snippet in `index.html` sets the class before the
  bundle runs, mirroring `resolveTheme`. Keep the two in step if the rule changes.
- Per-scheme `theme-color` meta tags keep the mobile browser chrome matching the page.

## Token groups (see `theme.css`)

- **Palette** — `--bg`, three `--aurora-*` blobs, `--text` / `--text-muted` /
  `--text-faint`, `--surface-glass` + `--surface-1..3`, `--hairline`, `--accent` (+
  `-strong` / `-soft`), `--success`, `--danger` (+ `-soft`), `--link`. Defined on
  `:root` (light) and overridden on `.dark`.
- **Glass material** — three blur tiers: `--blur-thin | -regular | -thick`. A sheet
  reads heavier than a card because it uses a thicker tier, not a bespoke number.
- **Shape** — `--radius-control | -card | -sheet | -pill`.
- **Motion** — `--dur-fast | -dur`, `--ease`.
- **Elevation** — `--shadow-card`, `--shadow-pop` (stacked, softer in light).

## Performance & accessibility budget

- **Glass budget:** `backdrop-filter` is the most expensive paint. Keep glass to the
  surfaces above; under `prefers-reduced-transparency` every glass surface falls back
  to an opaque one automatically.
- **Aurora:** a single fixed layer animating only `transform`; it stops under
  `prefers-reduced-motion`.
- **Focus:** one `:focus-visible` ring covers every custom control. Controls carry the
  right roles/labels (`aria-pressed` on toggles/segments, `aria-label` on sliders).

## Layout

- `AppShell` renders the sticky glass top bar above every page; pages keep their own
  `<main className="app">` beneath it.
- Guild sub-pages wrap their body in `GuildShell`, which lays out `GuildNav` as a
  sticky sidebar rail at ≥860px and a horizontally scrolling pill row on phones.
