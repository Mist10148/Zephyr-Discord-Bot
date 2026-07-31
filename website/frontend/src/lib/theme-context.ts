import { createContext, useContext } from 'react'

// Non-component members of the theme system live here so lib/theme.tsx can export
// only the ThemeProvider component (keeping React Fast Refresh happy). The whole
// theme is a single `.dark` class on <html>; the pre-paint snippet in index.html
// sets it before React mounts, and resolveTheme() below is the same rule that
// snippet inlines.
export type Theme = 'light' | 'dark'
export const STORAGE_KEY = 'zephyr-theme'

export function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function resolveTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch { /* Safari private mode throws on localStorage; fall through to the OS. */ }
  return systemTheme()
}

export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  document.documentElement.style.colorScheme = theme
}

export type ThemeContextValue = { theme: Theme; setTheme(theme: Theme): void; toggle(): void }
export const ThemeContext = createContext<ThemeContextValue | null>(null)

export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) throw new Error('useTheme must be used within a ThemeProvider')
  return context
}
