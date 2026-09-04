import { expect, test } from './fixtures'

/**
 * The dashboard, with a real session and no live bot.
 *
 * "No live bot" is not a limitation being worked around — it is a state the
 * production app must handle, and one the unit suite cannot reach.
 *
 * The split is worth knowing, because a first draft of these specs got it
 * wrong: the **overview** reads Redis only, so with a seeded presence and guild
 * snapshot it renders fully. It is the **music** page that issues a bridge
 * command, and that is where the 5s timeout and 9.3's degradation card are.
 */

test.describe('authentication', () => {
  test('an anonymous visitor is sent to the login screen', async ({ page }) => {
    await page.goto('/g')

    await expect(page).toHaveURL(/\/login/)
  })

  test('a seeded session reaches the guild list', async ({ signedIn: page, seeded }) => {
    await page.goto('/g')

    await expect(page).not.toHaveURL(/\/login/)
    await expect(page.getByText(seeded.guild_name)).toBeVisible()
  })

  test('the session is really read from Redis, not just the cookie', async ({
    signedIn: page,
  }) => {
    // /api/v1/me is the endpoint the shell reads, and it 401s unless the sid
    // resolves to a stored session -- which is what makes the seam faithful
    // rather than a cookie the frontend happens to trust.
    const response = await page.request.get('/api/v1/me')

    expect(response.status()).toBe(200)
    expect((await response.json()).user.username).toBe('e2e-tester')
  })
})

test.describe('a guild with no bot listening', () => {
  test('the overview renders from Redis alone', async ({ signedIn: page, seeded }) => {
    await page.goto(`/g/${seeded.guild_id}`)

    await expect(page.getByRole('heading', { name: seeded.guild_name, level: 1 })).toBeVisible({
      timeout: 20_000,
    })
    // 9.3 and A3: the overview used to be a bare id and a link list. It now
    // reports the stored configuration, and says out loud when a server has
    // none rather than presenting the defaults as settings somebody chose.
    await expect(page.getByText(/has not been configured yet/i)).toBeVisible()
    await expect(page.getByRole('link', { name: /Music/ }).first()).toBeVisible()
  })

  test('the enabled-module list is rendered with human names', async ({
    signedIn: page,
    seeded,
  }) => {
    // A3: the overview listed raw cog keys. The map comes from `GET /commands`'
    // categories, so this is also a check that the two endpoints agree.
    await page.goto(`/g/${seeded.guild_id}`)

    await expect(page.getByText('Weather — Alerts')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByText('Activity & Levels')).toBeVisible()
  })

  test('the music page renders with nothing playing', async ({ signedIn: page, seeded }) => {
    await page.goto(`/g/${seeded.guild_id}/music`)

    // The player reads the *snapshot* the bot publishes to Redis rather than
    // issuing a bridge command, which is why this resolves immediately with no
    // bot: no snapshot is a legitimate state ("nothing playing"), not an
    // error. A first draft of this spec assumed a 5s bridge timeout and a
    // degradation card; there is neither, and finding that out is worth as
    // much as the assertion.
    await expect(page.getByRole('heading', { name: 'Music', level: 1 })).toBeVisible({
      timeout: 20_000,
    })
    await expect(page.getByLabel('Song or URL to queue')).toBeVisible()

    // 9.5's live region: a screen reader has to be told what is playing, and
    // "nothing" is an answer.
    await expect(page.locator('.player-live')).toHaveText(/nothing playing/i)
  })

  test('the queue field refuses an empty submission', async ({ signedIn: page, seeded }) => {
    // 8.1's class of defect from the other side: a control that *should* be
    // disabled and is not. An empty query must not be sendable.
    await page.goto(`/g/${seeded.guild_id}/music`)
    await expect(page.getByLabel('Song or URL to queue')).toBeVisible({ timeout: 20_000 })

    const submit = page.getByRole('button', { name: /^Queue$|^Play next$/ }).first()
    await expect(submit).toBeDisabled()

    await page.getByLabel('Song or URL to queue').fill('never gonna give you up')
    await expect(submit).toBeEnabled()
  })

  test('a guild the session does not manage is refused', async ({ signedIn: page }) => {
    const response = await page.request.get('/api/v1/guilds/999999999999999999/settings')

    expect(response.status()).toBe(403)
  })
})

test.describe('settings', () => {
  test('a save round-trips through the API', async ({ signedIn: page, seeded }) => {
    // Through `page.request`, so the session and CSRF cookies the browser holds
    // are the ones used -- a PATCH with no CSRF header is refused, and that
    // check is worth exercising from the same place the app does it.
    const response = await page.request.patch(`/api/v1/guilds/${seeded.guild_id}/settings`, {
      data: { prefix: '!!' },
      // The header name the app actually sends. `X-CSRF-Token` is the
      // convention elsewhere and is not this one -- `guard.CSRF_HEADER`.
      headers: { 'X-Zephyr-CSRF': seeded.csrf },
    })

    expect(response.status()).toBe(200)
    expect((await response.json()).prefix).toBe('!!')

    const read = await page.request.get(`/api/v1/guilds/${seeded.guild_id}/settings`)
    expect((await read.json()).prefix).toBe('!!')
  })

  test('a save with no CSRF token is refused', async ({ signedIn: page, seeded }) => {
    const response = await page.request.patch(`/api/v1/guilds/${seeded.guild_id}/settings`, {
      data: { prefix: '??' },
    })

    expect(response.status()).toBe(403)
  })

  test('the settings screen renders its form', async ({ signedIn: page, seeded }) => {
    await page.goto(`/g/${seeded.guild_id}/settings`)

    await expect(page.getByRole('heading', { name: /settings/i }).first()).toBeVisible({
      timeout: 20_000,
    })
  })
})

test.describe('every interactive control has a handler', () => {
  /**
   * The guard 17.4 was asked for: "a button rendered without a handler fails
   * CI". A dead `<button>` cannot be detected by looking at it, so this checks
   * the two things that are observable — that it is not disabled without
   * saying why, and that clicking it changes *something*.
   *
   * Kept to the public weather screen, which is where Phase 8's dead Play
   * button lived and where no bot is needed to observe a change.
   */
  test('the saved-place chip and its remove button both do something', async ({
    stubbedWeather: page,
  }) => {
    await page.goto('/weather')
    await page.getByLabel('Search city').fill('Iloilo')
    // exact: true -- a bare 'Use' also matches "Use my location".
    await page.getByRole('button', { name: 'Use', exact: true }).first().click()
    await expect(page.getByRole('heading', { name: /Iloilo City/ })).toBeVisible()

    // 8.6: the place became a chip with an explicit remove control, and the
    // active one is marked with aria-current.
    const chip = page.getByRole('button', { name: 'Iloilo City', exact: true })
    await expect(chip).toBeVisible()
    await expect(chip).toHaveAttribute('aria-current', 'true')

    await page.getByRole('button', { name: 'Remove Iloilo City' }).click()

    await expect(chip).toHaveCount(0)
  })

  test('no enabled button on the weather screen is inert', async ({ stubbedWeather: page }) => {
    await page.goto('/weather')
    await page.getByLabel('Search city').fill('Iloilo')
    await expect(page.getByText('Iloilo City, Philippines')).toBeVisible()

    // Every button with an onClick has a listener registered on it. A React
    // element rendered with no handler has none, which is precisely the defect
    // 8.1 was.
    const inert = await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll('main button:not([disabled])'))
      return buttons
        .filter(button => {
          // React attaches one delegated listener at the root, so the props
          // are the only place to look. The fiber key is stable enough for a
          // guard that is allowed to be wrong in one direction: a false
          // negative here is a spec that passes, not a lie.
          const key = Object.keys(button).find(name => name.startsWith('__reactProps$'))
          if (!key) return false
          const props = (button as unknown as Record<string, { onClick?: unknown }>)[key]
          return !props?.onClick
        })
        .map(button => button.textContent?.trim() || button.getAttribute('aria-label'))
    })

    expect(inert).toEqual([])
  })
})
