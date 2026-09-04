// The design's inline SVGs, extracted once so no route hand-rolls a path.
//
// Every icon draws in `currentColor` rather than naming a token directly: the same
// glyph appears in accent, muted and danger depending on where it sits (the skip
// arrow is --text on the kitchen-sink row and --text-muted in the transport), and
// inheriting colour is what lets one component serve all of those. Sizes are props
// because the design uses the same shapes at 20, 26, 28 and 64px.
//
// All of them are decorative: the surrounding button carries the accessible name,
// so each root gets aria-hidden and no <title>.

type IconProps = { size?: number; className?: string }

const base = (size: number) => ({ width: size, height: size, 'aria-hidden': true as const, focusable: 'false' as const })

/* ---- Weather ---------------------------------------------------------------- */

export function SunIcon({ size = 28, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round">
    <circle cx="12" cy="12" r="4.6" />
    <line x1="12" y1="2.4" x2="12" y2="4.4" /><line x1="12" y1="19.6" x2="12" y2="21.6" />
    <line x1="2.4" y1="12" x2="4.4" y2="12" /><line x1="19.6" y1="12" x2="21.6" y2="12" />
    <line x1="5.6" y1="5.6" x2="7" y2="7" /><line x1="17" y1="17" x2="18.4" y2="18.4" />
    <line x1="5.6" y1="18.4" x2="7" y2="17" /><line x1="17" y1="7" x2="18.4" y2="5.6" />
  </svg>
}

export function CloudIcon({ size = 28, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.4" fill="none">
    <rect x="3" y="10" width="18" height="8.4" rx="4.2" />
    <circle cx="9" cy="11.4" r="4.2" /><circle cx="15.5" cy="12" r="3.4" />
  </svg>
}

export function RainIcon({ size = 28, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round">
    <rect x="3.5" y="6" width="17" height="8" rx="4" /><circle cx="9.5" cy="7.6" r="4" />
    <line x1="8" y1="17" x2="7" y2="20.5" /><line x1="13" y1="17" x2="12" y2="20.5" /><line x1="18" y1="17" x2="17" y2="20.5" />
  </svg>
}

/** The hero glyph on the weather page — sun tucked behind a cloud. */
export function SunCloudIcon({ size = 64, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.3" fill="none" strokeLinecap="round">
    <circle cx="8.5" cy="7.5" r="3.2" />
    <line x1="8.5" y1="1.4" x2="8.5" y2="2.8" /><line x1="2.6" y1="7.5" x2="1.2" y2="7.5" />
    <line x1="4.3" y1="3.3" x2="3.3" y2="2.3" /><line x1="12.7" y1="3.3" x2="13.7" y2="2.3" />
    <rect x="7.5" y="12" width="14" height="8" rx="4" /><circle cx="11.5" cy="13.6" r="3.6" />
  </svg>
}

/* ---- Home feature cards ----------------------------------------------------- */

export function FeatureWeatherIcon({ size = 26, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round">
    <circle cx="9" cy="8" r="3.4" />
    <line x1="9" y1="1.6" x2="9" y2="3" /><line x1="3.4" y1="8" x2="1.8" y2="8" /><line x1="4.9" y1="3.9" x2="3.8" y2="2.8" />
    <rect x="8" y="12" width="13" height="7.5" rx="3.75" /><circle cx="11.5" cy="13.4" r="3.4" />
  </svg>
}

export function FeatureDashboardIcon({ size = 26, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5" fill="none">
    <rect x="2.6" y="4" width="18.8" height="5" rx="2.5" /><rect x="2.6" y="12" width="18.8" height="5" rx="2.5" />
    <circle cx="7" cy="6.5" r="1" /><circle cx="7" cy="14.5" r="1" />
  </svg>
}

export function FeatureSystemIcon({ size = 26, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5" fill="none">
    <rect x="3" y="3" width="18" height="18" rx="5.5" /><line x1="3" y1="10" x2="21" y2="10" /><circle cx="7" cy="6.5" r="1.1" />
  </svg>
}

/* ---- Transport -------------------------------------------------------------- */
// Replaces the old text glyphs (❙❙ ▶︎ ⏭ 🔀 ⏹), which rendered differently on every
// platform and could not be tinted.

export function PauseIcon({ size = 16, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 16 16" fill="currentColor">
    <rect x="3" y="2.5" width="3.4" height="11" rx="1.4" /><rect x="9.6" y="2.5" width="3.4" height="11" rx="1.4" />
  </svg>
}

export function PlayIcon({ size = 16, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 16 16" fill="currentColor"><path d="M4.4 2.8v10.4L13 8z" /></svg>
}

export function SkipIcon({ size = 16, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 16 16" fill="currentColor">
    <path d="M3 3.2v9.6L10 8z" /><rect x="11" y="3.2" width="2.4" height="9.6" rx="1.2" />
  </svg>
}

export function ShuffleIcon({ size = 17, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 18 18" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round">
    <path d="M2.5 4.5h3l7 9h2.6" /><path d="M2.5 13.5h3l7-9h2.6" />
    <path d="M13.2 2.7l1.9 1.8-1.9 1.8" /><path d="M13.2 11.7l1.9 1.8-1.9 1.8" />
  </svg>
}

export function StopIcon({ size = 14, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 16 16" fill="currentColor"><rect x="3" y="3" width="10" height="10" rx="2.4" /></svg>
}

/* ---- Tab bar ---------------------------------------------------------------- */

export function TabHomeIcon({ size = 20, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 20 20"><circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
}

export function TabWeatherIcon({ size = 20, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 20 20" stroke="currentColor" strokeWidth="1.6" fill="none">
    <circle cx="7.5" cy="7.5" r="3" /><rect x="7" y="10.5" width="10" height="5" rx="2.5" /><circle cx="10" cy="11" r="2.6" />
  </svg>
}

export function TabServersIcon({ size = 20, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 20 20" stroke="currentColor" strokeWidth="1.6" fill="none">
    <rect x="3" y="4" width="14" height="4" rx="2" /><rect x="3" y="12" width="14" height="4" rx="2" />
  </svg>
}

export function TabSystemIcon({ size = 20, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 20 20" stroke="currentColor" strokeWidth="1.6" fill="none">
    <rect x="3.5" y="3.5" width="13" height="13" rx="4" /><line x1="3.5" y1="10" x2="16.5" y2="10" />
  </svg>
}

/* ---- Appearance ------------------------------------------------------------- */
// The moon is a filled disc with a second disc punched out of it in the surface
// colour, so it stays a crescent in both themes without a mask.

export function MoonIcon({ size = 16, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 16 16">
    <circle cx="8" cy="8" r="5.5" fill="currentColor" /><circle cx="12" cy="4.5" r="4.5" fill="var(--surface-1)" />
  </svg>
}

export function SunSmallIcon({ size = 17, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 18 18" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" fill="none">
    <circle cx="9" cy="9" r="3.4" />
    <line x1="9" y1="1.4" x2="9" y2="3.2" /><line x1="9" y1="14.8" x2="9" y2="16.6" />
    <line x1="1.4" y1="9" x2="3.2" y2="9" /><line x1="14.8" y1="9" x2="16.6" y2="9" />
    <line x1="3.8" y1="3.8" x2="5" y2="5" /><line x1="13" y1="13" x2="14.2" y2="14.2" />
    <line x1="3.8" y1="14.2" x2="5" y2="13" /><line x1="13" y1="5" x2="14.2" y2="3.8" />
  </svg>
}

// The now-playing art fallback. The placeholder used to render the literal
// words "track art" in 8px mono where the thumbnail should be.
export function DiscIcon({ size = 28, className }: IconProps) {
  return <svg {...base(size)} className={className} viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5" fill="none">
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="12" r="2.4" fill="currentColor" stroke="none" />
    <path d="M12 5.6a6.4 6.4 0 0 1 6.4 6.4" strokeLinecap="round" />
  </svg>
}
