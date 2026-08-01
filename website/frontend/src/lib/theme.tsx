import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { applyPreferences, readPreferences, resolvedTheme, writePreferences, type Preferences } from './preferences'
import { ThemeContext, type Theme } from './theme-context'

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preferences, setPreferencesState] = useState<Preferences>(() => typeof document === 'undefined' ? readPreferences() : readPreferences())
  const setPreferences = useCallback((next: Preferences) => { setPreferencesState(next); writePreferences(next); applyPreferences(next) }, [])
  const patchPreferences = useCallback((patch: Partial<Omit<Preferences, 'version'>>) => setPreferences({ ...preferences, ...patch, version: 2 }), [preferences, setPreferences])
  const toggle = useCallback(() => patchPreferences({ theme: resolvedTheme(preferences) === 'dark' ? 'light' : 'dark' }), [patchPreferences, preferences])
  useEffect(() => { applyPreferences(preferences) }, [preferences])
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => preferences.theme === 'system' && applyPreferences(preferences)
    media.addEventListener('change', onChange); return () => media.removeEventListener('change', onChange)
  }, [preferences])
  const theme: Theme = resolvedTheme(preferences)
  return <ThemeContext.Provider value={{ theme, preferences, setPreferences, patchPreferences, toggle }}>{children}</ThemeContext.Provider>
}
