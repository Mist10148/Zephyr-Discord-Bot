import { CONDITIONS, expect, test } from './fixtures'

/**
 * The public site, end to end through the real Flask app and the real bundle.
 *
 * Every one of these would have caught a Phase 8 defect that the unit suite
 * could not: a search box whose query was keyed on the wrong value, a list that
 * showed "no matching places" while the fetch was in flight, an error branch
 * that was never rendered because `ErrorNote` was not imported.
 */

test.describe('the home page', () => {
  test('renders and offers the invite', async ({ page }) => {
    await page.goto('/')

    // The invite is 12.1's whole point: it was buried, and it is what a new
    // visitor is there for.
    await expect(page.getByRole('link', { name: /add zephyr|invite/i }).first()).toBeVisible()
  })

  test('reports the bot as online from the seeded heartbeat', async ({ page, seeded }) => {
    expect(seeded.sid).toBeTruthy()
    await page.goto('/')

    await expect(page.getByText(/online/i).first()).toBeVisible()
  })
})

test.describe('the weather search', () => {
  test('a typed query lists places and a chosen place renders conditions', async ({
    stubbedWeather: page,
  }) => {
    await page.goto('/weather')

    const search = page.getByLabel('Search city')
    await expect(search).toBeVisible()

    // Under two letters the list must say so rather than search: the debounce
    // and the `enabled` guard are both on this path.
    await search.fill('I')
    await expect(page.getByText('Type at least two letters')).toBeVisible()

    await search.fill('Iloilo')
    await expect(page.getByText('Iloilo City, Philippines')).toBeVisible()

    // The button that had no handler. Clicking it has to produce conditions.
    //
    // `exact: true` matters: getByRole matches the accessible name by
    // substring, so a bare 'Use' also matches "Use my location" -- which is
    // *first* in the DOM, so the click asked the browser for geolocation and
    // the spec then waited for conditions that were never requested.
    await page.getByRole('button', { name: 'Use', exact: true }).first().click()

    await expect(page.getByRole('heading', { name: /Iloilo City/ })).toBeVisible()
    // Read from the fixture rather than written out, so the assertion cannot
    // disagree with what the endpoint actually sends.
    await expect(page.locator('.current-desc')).toHaveText(CONDITIONS.current.description)
    await expect(page.locator('.current-temp')).toContainText(
      String(Math.round(CONDITIONS.current.temperature)),
    )
  })

  test('a failing search renders the error branch and can retry', async ({ page }) => {
    // 8.5: `weather.isError` had no branch at all, and `ErrorNote` was not even
    // imported into this screen -- so a failed request rendered nothing.
    // A flag rather than an attempt counter. `createQueryClient` retries a 5xx
    // once, so failing only the *first* request meant the retry succeeded and
    // the error branch never rendered -- the app behaving correctly and the
    // spec being wrong.
    let failing = true
    await page.route('**/api/v1/geocode*', route =>
      failing
        ? route.fulfill({ status: 500, json: { error: { code: 'upstream', message: 'nope' } } })
        : route.fulfill({ json: { results: [{ name: 'Manila', country: 'Philippines', latitude: 14.6, longitude: 120.98 }] } }),
    )

    await page.goto('/weather')
    await page.getByLabel('Search city').fill('Manila')

    await expect(page.getByText('Could not search for places')).toBeVisible()

    failing = false
    await page.getByRole('button', { name: 'Try again' }).click()

    await expect(page.getByText('Manila, Philippines')).toBeVisible()
  })

  test('a search with no matches says so only once the fetch has landed', async ({ page }) => {
    /**
     * The exact Phase 8 defect: the empty case was tested before the pending
     * case, so every keystroke flashed "No matching places" while the request
     * was in flight.
     */
    await page.route('**/api/v1/geocode*', async route => {
      await new Promise(resolve => setTimeout(resolve, 400))
      await route.fulfill({ json: { results: [] } })
    })

    await page.goto('/weather')
    await page.getByLabel('Search city').fill('Zzzzz')

    await expect(page.getByText('Searching…')).toBeVisible()
    await expect(page.getByText('No matching places')).toBeVisible()
  })
})

test.describe('the command reference', () => {
  test('lists commands from the API', async ({ page }) => {
    await page.goto('/commands')

    await expect(page.getByText('/play', { exact: false }).first()).toBeVisible()
  })
})

test.describe('routing', () => {
  test('an unknown path is a real 404, not a soft one', async ({ page }) => {
    // 12.5: every unknown path answered 200 with the NotFound screen, so a
    // crawler was told the site had a hundred pages that all say "page not
    // found".
    const response = await page.goto('/definitely-not-a-route')

    expect(response?.status()).toBe(404)
  })

  test('a known public path answers 200', async ({ page }) => {
    const response = await page.goto('/privacy')

    expect(response?.status()).toBe(200)
    await expect(page.getByRole('heading', { name: /privacy/i }).first()).toBeVisible()
  })

  test('robots.txt points at the sitemap and hides the private routes', async ({ request }) => {
    const response = await request.get('/robots.txt')
    const body = await response.text()

    expect(response.status()).toBe(200)
    expect(body).toContain('Sitemap:')
    expect(body).toContain('Disallow: /g')
    expect(body).toContain('Disallow: /api/')
  })

  test('the sitemap lists only the public routes', async ({ request }) => {
    const body = await (await request.get('/sitemap.xml')).text()

    expect(body).toContain('/weather')
    expect(body).toContain('/privacy')
    // The dashboard is behind auth and renders an empty shell to a crawler.
    expect(body).not.toContain('/kitchen-sink')
    expect(body).not.toContain('<loc>http://127.0.0.1:5001/g<')
  })
})
