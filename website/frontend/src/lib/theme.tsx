import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'

// The whole theme is a single class on <html>: `.dark` present = dark, absent =
// light. That is the one thing theme.css keys off, and it is also what the
// no-FOUC snippet in index.html sets before React ever mounts -- so this provider
// adopts whatever that snippet already decided rather than fighting it on load.
export type Theme = 'light' | 'dark'
const STORAGE_KEY = 'zephyr-theme'

function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

// Exported so the pre-React inline script and this module agree on the rule:
// an explicit stored choice wins; otherwise follow the OS. Kept dependency-free
// so index.html can inline the same logic.
export function resolveTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch { /* Safari private mode throws on localStorage; fall through to the OS. */ }
  return systemTheme()
}

function apply(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  // Keep the browser UI (form controls, scrollbars) in step with the page.
  document.documentElement.style.colorScheme = theme
}

type ThemeContext = { theme: Theme; setTheme(theme: Theme): void; toggle(): void }
const Context = createContext<ThemeContext | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => (typeof document === 'undefined' ? 'dark' : resolveTheme()))

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next)
    apply(next)
    try { localStorage.setItem(STORAGE_KEY, next) } catch { /* ignore */ }
  }, [])

  const toggle = useCallback(() => setTheme(theme === 'dark' ? 'light' : 'dark'), [theme, setTheme])

  // Follow the OS only while the user has expressed no explicit preference.
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => {
      let stored: string | null = null
      try { stored = localStorage.getItem(STORAGE_KEY) } catch { /* ignore */ }
      if (!stored) setThemeState(systemTheme())  // the apply() effect below reacts to the state change
    }
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  useEffect(() => { apply(theme) }, [theme])

  return <Context.Provider value={{ theme, setTheme, toggle }}>{children}</Context.Provider>
}

export function useTheme() {
  const context = useContext(Context)
  if (!context) throw new Error('useTheme must be used within a ThemeProvider')
  return context
}
