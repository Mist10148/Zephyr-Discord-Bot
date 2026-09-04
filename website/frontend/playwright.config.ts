import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end config.
 *
 * `testDir` is `./e2e`, deliberately not `./test`: that is `vitest.config.ts`'s
 * include glob, and a Playwright spec collected by vitest fails on `import
 * { test } from '@playwright/test'` in a jsdom environment. The two suites
 * answer different questions and share no files.
 *
 * The whole point of this suite is that every Phase 8 defect was a *wiring*
 * defect — a button with no `onClick`, a query keyed on the wrong value, an
 * error branch never read. Unit tests on primitives cannot see those, because
 * each primitive was fine.
 *
 * The server is the real Flask app serving the real built bundle. Only the two
 * endpoints that would reach a third party (`/geocode`, `/weather`) are
 * intercepted in the browser, so the wiring is exercised end to end while CI
 * does not depend on Open-Meteo being up. That trade-off is stated in the spec
 * files where it applies.
 */
export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  // A wiring defect is deterministic, so a retry only hides a flake. If a spec
  // here is flaky, the spec is wrong.
  retries: 0,
  fullyParallel: false,
  // One worker: the specs share one Flask process and one Redis, and a
  // parallel run would have two of them writing the same session key.
  workers: 1,
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:5001',
    // The built bundle registers a Workbox service worker, which intercepts
    // fetches before `page.route` can see them -- so a route stub silently did
    // nothing and every stubbed spec failed against the real endpoint. Blocked
    // rather than worked around: a service worker serving a precached shell is
    // also non-deterministic between runs, which is the opposite of what a
    // wiring test wants.
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        // From the repo root: `run_web.py` is the same entry point production
        // uses, so this cannot pass against a configuration nothing ships.
        command: 'python run_web.py',
        cwd: '../..',
        url: 'http://127.0.0.1:5001/api/v1/site',
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
        stdout: 'pipe',
        stderr: 'pipe',
        env: {
          FLASK_HOST: '127.0.0.1',
          PORT: '5001',
          // Enough to satisfy validate_web_config. No upstream call is made:
          // the two endpoints that would are intercepted in the browser.
          OPENWEATHER_API_KEY: 'e2e-not-a-real-key',
          // The dashboard exists only when all three are set, and half the
          // suite is the dashboard.
          DISCORD_CLIENT_ID: '100000000000000001',
          DISCORD_CLIENT_SECRET: 'e2e-secret',
          REDIS_URL: process.env.REDIS_URL || 'redis://127.0.0.1:6379/9',
          // The throwaway database `global-setup.ts` has just deleted, not the
          // developer's `data/zephyr.db`. SQLite, so `should_auto_create`
          // (17.3) builds the current schema without running Alembic.
          DATABASE_URL:
            process.env.E2E_DATABASE_URL ||
            'sqlite:///website/frontend/e2e/.e2e/zephyr.db',
          WEB_PUBLIC_URL: 'http://127.0.0.1:5001',
          // Over http, so the cookie has to be allowed to be non-secure or the
          // browser drops it and every dashboard spec redirects to /login.
          AUTH_COOKIE_SECURE: '0',
          SUPPORT_URL: 'https://discord.gg/e2e',
          REPOSITORY_URL: 'https://github.com/e2e/zephyr',
        },
      },
})
