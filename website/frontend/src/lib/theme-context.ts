import { createContext, useContext } from 'react'
import type { Preferences } from './preferences'

export type Theme = 'light' | 'dark'
export type ThemeContextValue = {
  theme: Theme; preferences: Preferences; setPreferences(next: Preferences): void
  patchPreferences(next: Partial<Omit<Preferences, 'version'>>): void; toggle(): void
}
export const ThemeContext = createContext<ThemeContextValue | null>(null)
export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) throw new Error('useTheme must be used within a ThemeProvider')
  return context
}
