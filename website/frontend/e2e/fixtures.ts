import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test as base, expect, type Page } from '@playwright/test'


// `package.json` sets "type": "module", so there is no `__dirname` here.
const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(HERE, '..', '..', '..')

// Read at runtime rather than imported: Node's ESM loader requires an
// `with { type: 'json' }` attribute, and TypeScript's `resolveJsonModule`
// rewrites the specifier in a way that loses it.
type Conditions = {
  current: { description: string; temperature: number }
  daily: unknown[]
  hourly: unknown[]
}
const CONDITIONS = JSON.parse(
  readFileSync(path.join(HERE, 'fixtures', 'weather.json'), 'utf8'),
) as Conditions

export type Seed = {
  sid: string
  csrf: string
  auth_cookie: string
  csrf_cookie: string
  guild_id: string
  guild_name: string
}

/**
 * Run the Python seeder and return what it printed.
 *
 * Shelling out rather than reimplementing session creation in TypeScript: the
 * session format, its TTL and its cookie names are Python's business, and a
 * second implementation here would drift from the first the moment either
 * changed — silently, because a wrong session just redirects to /login and
 * looks like a broken test.
 */
function seed(): Seed {
  const python = process.env.PYTHON || 'python'
  const output = execFileSync(python, ['website/frontend/e2e/seed.py'], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    env: { ...process.env, REDIS_URL: process.env.REDIS_URL || 'redis://127.0.0.1:6379/9' },
  })
  const lines = output.trim().split('\n')
  return JSON.parse(lines[lines.length - 1]) as Seed
}

/** Place results for the search box, so no spec depends on Open-Meteo. */
const PLACES = {
  results: [
    { name: 'Iloilo City', country: 'Philippines', latitude: 10.72, longitude: 122.56 },
    { name: 'Manila', country: 'Philippines', latitude: 14.6, longitude: 120.98 },
  ],
}

/**
 * The real `/weather` response, captured from the endpoint with a stubbed
 * provider.
 *
 * Hand-written first, and that was a mistake worth recording: the guessed shape
 * used `daily[].date` where the app sends `time_local`, so clicking a place
 * threw inside the render and the page fell into its error boundary. The spec
 * reported "no heading", which looks like a missing element and was actually a
 * crash. A fixture that diverges from the response makes the E2E test a test of
 * the fixture.
 *
 * `tests/test_e2e_fixtures.py` fails if the endpoint's keys stop matching this
 * file, so it cannot drift again silently.
 */


export const test = base.extend<{
  seeded: Seed
  signedIn: Page
  stubbedWeather: Page
}>({
  seeded: async ({}, use) => {
    await use(seed())
  },

  /**
   * A page with a real session already attached.
   *
   * The cookies are set on the context rather than through a login flow,
   * because the login flow is Discord's and driving it in CI would need a real
   * OAuth application and a real account.
   */
  signedIn: async ({ context, page, seeded }, use) => {
    await context.addCookies([
      { name: seeded.auth_cookie, value: seeded.sid, url: 'http://127.0.0.1:5001' },
      { name: seeded.csrf_cookie, value: seeded.csrf, url: 'http://127.0.0.1:5001' },
    ])
    await use(page)
  },

  /**
   * A page where the two upstream-dependent endpoints answer from fixtures.
   *
   * Intercepted in the *browser*, not in Flask: everything between the click
   * and the request still runs — the debounce, the query key, the four-state
   * list, the render — which is exactly the wiring this suite exists to check.
   * Only the third party is absent.
   */
  stubbedWeather: async ({ page }, use) => {
    await page.route('**/api/v1/geocode*', route =>
      route.fulfill({ json: PLACES, headers: { 'cache-control': 'no-store' } }),
    )
    await page.route('**/api/v1/weather*', route =>
      route.fulfill({ json: CONDITIONS, headers: { 'cache-control': 'no-store' } }),
    )
    await use(page)
  },
})

export { expect, PLACES, CONDITIONS }
