import { beforeEach, describe, expect, it } from 'vitest'
import { DEFAULT_PREFERENCES, LEGACY_THEME_KEY, PREFERENCES_KEY, readPreferences } from '../src/lib/preferences'

describe('website preferences', () => {
  beforeEach(() => localStorage.clear())
  it('migrates the old theme choice into the v2 defaults', () => {
    localStorage.setItem(LEGACY_THEME_KEY, 'dark')
    expect(readPreferences()).toMatchObject({ ...DEFAULT_PREFERENCES, theme: 'dark' })
  })
  it('rejects malformed persisted values instead of applying unknown UI states', () => {
    localStorage.setItem(PREFERENCES_KEY, JSON.stringify({ theme: 'neon', palette: 'nope', dashboardView: 'list' }))
    expect(readPreferences()).toMatchObject({ theme: 'system', palette: 'warm', dashboardView: 'list' })
  })
})
