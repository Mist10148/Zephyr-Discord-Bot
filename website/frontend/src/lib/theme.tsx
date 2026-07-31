import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { applyTheme, resolveTheme, STORAGE_KEY, systemTheme, ThemeContext, type Theme } from './theme-context'

// The provider adopts whatever the pre-paint snippet in index.html already decided,
// then keeps <html>'s `.dark` class, localStorage and the OS preference in sync.
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => (typeof document === 'undefined' ? 'dark' : resolveTheme()))

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next)
    applyTheme(next)
    try { localStorage.setItem(STORAGE_KEY, next) } catch { /* ignore */ }
  }, [])

  const toggle = useCallback(() => setTheme(theme === 'dark' ? 'light' : 'dark'), [theme, setTheme])

  // Follow the OS only while the user has expressed no explicit preference.
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => {
      let stored: string | null = null
      try { stored = localStorage.getItem(STORAGE_KEY) } catch { /* ignore */ }
      if (!stored) setThemeState(systemTheme())  // the effect below applies the class
    }
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  useEffect(() => { applyTheme(theme) }, [theme])

  return <ThemeContext.Provider value={{ theme, setTheme, toggle }}>{children}</ThemeContext.Provider>
}
