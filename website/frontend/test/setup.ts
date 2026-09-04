// jest-dom adds matchers like toBeInTheDocument / toHaveAttribute and registers
// them with Vitest's expect. Imported once here via the config's setupFiles.
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

// Everything below is a jsdom gap, not a convenience. jsdom implements none of
// these, and each one is reached by ordinary app code:
//
//  * matchMedia      -- preferences.systemTheme() and ThemeProvider call it on
//                       mount, so *any* spec that renders a themed tree throws
//                       without it. This is why there were only three specs.
//  * geolocation     -- Weather's "Use my location".
//  * clipboard       -- the command palette and the command reference copy rows.
//  * scrollIntoView  -- the category jump list.
//  * IntersectionObserver / ResizeObserver -- the active-chip tracker and dnd-kit.
//
// They are installed as plain writable properties rather than vi.spyOn because
// they do not exist to be spied on; a spec that wants to assert on a call
// re-stubs the one it cares about.

function defineOnce(target: object, property: string, value: unknown) {
  // configurable so a spec can override it for one case and monkeypatch back.
  Object.defineProperty(target, property, { value, writable: true, configurable: true })
}

class StubObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return [] }
}

beforeEach(() => {
  defineOnce(window, 'matchMedia', (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    // Deprecated, but ThemeProvider may still reach for them on older paths.
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }))

  // The three codes are the ones locate()'s error branch distinguishes. jsdom
  // does not define GeolocationPositionError, so a spec cannot reach them from
  // the global -- they are on the object the callback receives, as in a browser.
  defineOnce(navigator, 'geolocation', {
    getCurrentPosition: vi.fn(),
    watchPosition: vi.fn(),
    clearWatch: vi.fn(),
  })

  defineOnce(navigator, 'clipboard', { writeText: vi.fn(() => Promise.resolve()) })
  defineOnce(Element.prototype, 'scrollIntoView', vi.fn())
  defineOnce(window, 'IntersectionObserver', StubObserver)
  defineOnce(window, 'ResizeObserver', StubObserver)

  // Preferences, saved weather places and guild pins all live here, and jsdom
  // keeps one store for the whole file. Without this, a spec that writes a
  // preference changes the theme of every spec after it in the same file.
  localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})
