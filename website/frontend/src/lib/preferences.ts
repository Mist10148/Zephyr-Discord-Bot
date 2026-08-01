export type ThemeMode = 'light' | 'system' | 'dark'
export type Palette = 'warm' | 'twilight' | 'forest'
export type Density = 'comfortable' | 'compact'
export type TextScale = '90' | '100' | '110'
export type MotionPreference = 'system' | 'reduced'
export type Units = 'metric' | 'imperial'
export type DashboardView = 'cards' | 'list'
export type GuildCategory = 'all' | 'pinned' | 'installed' | 'needs-bot' | 'unknown'
export type GuildSort = 'pinned' | 'recent' | 'name-asc' | 'name-desc' | 'status'

export type Preferences = {
  version: 2; theme: ThemeMode; palette: Palette; density: Density; textScale: TextScale
  motion: MotionPreference; units: Units; dashboardView: DashboardView; guildCategory: GuildCategory; guildSort: GuildSort
}

export const PREFERENCES_KEY = 'zephyr-preferences-v2'
export const PINS_KEY = 'zephyr-pinned-guilds'
export const RECENTS_KEY = 'zephyr-recent-guilds'
export const LEGACY_THEME_KEY = 'zephyr-theme'
export const DEFAULT_PREFERENCES: Preferences = {
  version: 2, theme: 'system', palette: 'warm', density: 'comfortable', textScale: '100',
  motion: 'system', units: 'metric', dashboardView: 'cards', guildCategory: 'all', guildSort: 'pinned',
}

const valid = <T extends string>(value: unknown, values: readonly T[], fallback: T): T =>
  typeof value === 'string' && values.includes(value as T) ? value as T : fallback

export function readPreferences(): Preferences {
  try {
    const parsed = JSON.parse(localStorage.getItem(PREFERENCES_KEY) ?? '{}') as Partial<Preferences>
    const legacy = localStorage.getItem(LEGACY_THEME_KEY)
    return {
      version: 2,
      theme: valid(parsed.theme ?? legacy, ['light', 'system', 'dark'], DEFAULT_PREFERENCES.theme),
      palette: valid(parsed.palette, ['warm', 'twilight', 'forest'], DEFAULT_PREFERENCES.palette),
      density: valid(parsed.density, ['comfortable', 'compact'], DEFAULT_PREFERENCES.density),
      textScale: valid(parsed.textScale, ['90', '100', '110'], DEFAULT_PREFERENCES.textScale),
      motion: valid(parsed.motion, ['system', 'reduced'], DEFAULT_PREFERENCES.motion),
      units: valid(parsed.units, ['metric', 'imperial'], DEFAULT_PREFERENCES.units),
      dashboardView: valid(parsed.dashboardView, ['cards', 'list'], DEFAULT_PREFERENCES.dashboardView),
      guildCategory: valid(parsed.guildCategory, ['all', 'pinned', 'installed', 'needs-bot', 'unknown'], DEFAULT_PREFERENCES.guildCategory),
      guildSort: valid(parsed.guildSort, ['pinned', 'recent', 'name-asc', 'name-desc', 'status'], DEFAULT_PREFERENCES.guildSort),
    }
  } catch { return DEFAULT_PREFERENCES }
}

export function writePreferences(next: Preferences) { try { localStorage.setItem(PREFERENCES_KEY, JSON.stringify(next)) } catch { /* storage can be unavailable */ } }
export function systemTheme() { return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light' as const }
export function resolvedTheme(preferences: Preferences) { return preferences.theme === 'system' ? systemTheme() : preferences.theme }
export function applyPreferences(preferences: Preferences) {
  const theme = resolvedTheme(preferences)
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  root.style.colorScheme = theme
  root.dataset.palette = preferences.palette
  root.dataset.density = preferences.density
  root.dataset.textScale = preferences.textScale
  root.dataset.motion = preferences.motion
}

export function readIds(key: string): string[] { try { const value = JSON.parse(localStorage.getItem(key) ?? '[]'); return Array.isArray(value) ? value.filter(item => typeof item === 'string') : [] } catch { return [] } }
export function writeIds(key: string, ids: string[]) { try { localStorage.setItem(key, JSON.stringify(ids)) } catch { /* ignore */ } }
