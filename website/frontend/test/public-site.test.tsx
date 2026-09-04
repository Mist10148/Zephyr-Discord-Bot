import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { Home } from '../src/routes/Home'
import { Privacy, Terms } from '../src/routes/Legal'
import { SiteFooter } from '../src/components/SiteFooter'
import { metaFor, titleFor } from '../src/lib/seo'
import { renderWithQuery, stubApi } from './helpers'

const STATUS = (extra: Record<string, unknown> = {}) => ({
  bot: { online: true, published_at: 1_700_000_000 },
  invite_url: 'https://discord.com/oauth2/authorize?client_id=1',
  ...extra,
})

describe('the invite call to action', () => {
  it('is the primary hero action', async () => {
    stubApi({ '/status': { body: STATUS() } })
    renderWithQuery(<Home />)

    // Somebody landing on / was previously offered "check the weather" and no
    // way to install the thing at all.
    const invite = await screen.findByRole('link', { name: 'Add Zephyr to Discord' })
    expect(invite).toHaveAttribute('href', 'https://discord.com/oauth2/authorize?client_id=1')
    expect(invite.className).toContain('primary')
  })

  it('is absent rather than broken on a weather-only deployment', async () => {
    // DISCORD_CLIENT_ID unset: the API answers null, so there is no authorize
    // URL to link to.
    stubApi({ '/status': { body: STATUS({ invite_url: null }) } })
    renderWithQuery(<Home />)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Check the weather' })).toBeInTheDocument())
    expect(screen.queryByRole('link', { name: /Add Zephyr/ })).not.toBeInTheDocument()
  })
})

describe('the status pill', () => {
  it('says waking rather than offline when a heartbeat exists but is stale', async () => {
    // Render's free tier spins the web service down, so a cold visitor can see
    // a bot that has published before but is not currently up -- and telling
    // somebody deciding whether to install it "Bot offline" is both wrong and
    // the worst possible moment.
    stubApi({ '/status': { body: { bot: { online: false, published_at: 1_700_000_000 }, invite_url: null } } })
    renderWithQuery(<Home />)
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Waking up'))
  })

  it('still says offline when there has never been a heartbeat', async () => {
    stubApi({ '/status': { body: { bot: { online: false, published_at: null }, invite_url: null } } })
    renderWithQuery(<Home />)
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Bot offline'))
  })

  it('says online when it is', async () => {
    stubApi({ '/status': { body: STATUS() } })
    renderWithQuery(<Home />)
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Bot online'))
  })
})

describe('per-route metadata', () => {
  it('gives every route its own title', () => {
    // Every route shared one static <title>, so history and a row of tabs
    // could not tell /commands from /weather.
    expect(titleFor('/weather')).toBe('Weather · Zephyr')
    expect(titleFor('/commands')).toBe('Commands · Zephyr')
    expect(titleFor('/privacy')).toBe('Privacy · Zephyr')
  })

  it('does not suffix the home title twice', () => {
    expect(titleFor('/')).toBe('Zephyr — weather, music and AI for Discord')
  })

  it('matches a guild sub-page without a table entry per guild', () => {
    expect(metaFor('/g/123456789012345678/music').title).toBe('Music')
  })

  it('does not let the root swallow every path below it', () => {
    expect(titleFor('/weather')).not.toBe(titleFor('/'))
  })

  it('noindexes the internal and authenticated surfaces', () => {
    for (const path of ['/kitchen-sink', '/login', '/g', '/g/1/audit']) {
      expect(metaFor(path).robots).toBe('noindex')
    }
    expect(metaFor('/weather').robots).toBeUndefined()
  })

  it('treats an unknown path as not found', () => {
    expect(metaFor('/nonsense').robots).toBe('noindex')
  })
})

describe('the footer', () => {
  const render = (route = '/') => renderWithQuery(<SiteFooter />, { route })

  it('links the legal pages, which is where they get found', async () => {
    stubApi({ '/site': { body: { support_url: null, repository_url: 'https://github.com/x/y' } } })
    render()

    const footer = screen.getByRole('contentinfo')
    expect(within(footer).getByRole('link', { name: 'Privacy' })).toHaveAttribute('href', '/privacy')
    expect(within(footer).getByRole('link', { name: 'Terms' })).toHaveAttribute('href', '/terms')
    // Demoted from the landing page's hero grid in 11.4; still reachable.
    expect(within(footer).getByRole('link', { name: 'Design system' })).toBeInTheDocument()
    await waitFor(() => expect(within(footer).getByRole('link', { name: 'Source' })).toBeInTheDocument())
  })

  it('omits a link it has no URL for', async () => {
    stubApi({ '/site': { body: { support_url: null, repository_url: null } } })
    render()
    await waitFor(() => expect(screen.getByRole('contentinfo')).toBeInTheDocument())
    // Absent rather than dead: a deployment with no support server should show
    // no support link.
    expect(screen.queryByRole('link', { name: 'Support' })).not.toBeInTheDocument()
  })

  it('stays out of the dashboard and the sign-in screen', () => {
    stubApi({ '/site': { body: {} } })
    const first = render('/login')
    expect(screen.queryByRole('contentinfo')).not.toBeInTheDocument()
    first.unmount()

    render('/g/1/music')
    expect(screen.queryByRole('contentinfo')).not.toBeInTheDocument()
  })
})

describe('the legal pages', () => {
  it('renders the retention table the server sends', async () => {
    stubApi({
      '/legal': {
        body: {
          retention: [{ category: 'Saved playlists', detail: 'Titles and links you saved.' }],
          session_caveat: 'Sessions cannot be listed individually.',
          contact: null,
          deletion: { self_service: ['/export-my-data'], per_channel: ['/forget'] },
        },
      },
    })
    renderWithQuery(<Privacy />)

    // Fetched rather than duplicated: the table lives beside the code that
    // implements the deletion, so the published policy cannot drift from it.
    await waitFor(() => expect(screen.getByText('Saved playlists')).toBeInTheDocument())
    expect(screen.getByText(/cannot be listed individually/)).toBeInTheDocument()
  })

  it('names the two things a deletion keeps', async () => {
    stubApi({ '/legal': { body: { retention: [], session_caveat: '', contact: null, deletion: { self_service: [], per_channel: [] } } } })
    renderWithQuery(<Privacy />)
    // "Everything" that quietly excludes two things is not everything.
    await waitFor(() => expect(screen.getByText(/audit history stays for/)).toBeInTheDocument())
    expect(screen.getByText(/only your own messages go/)).toBeInTheDocument()
  })

  it('survives the legal endpoint failing', async () => {
    stubApi({ '/legal': { status: 503, body: { error: { code: 'x', message: 'Unavailable' } } } })
    renderWithQuery(<Privacy />)
    // The page's own text is static; only the table needs the server.
    await waitFor(() => expect(screen.getByText(/Unavailable/)).toBeInTheDocument())
    expect(screen.getByText(/does not/i)).toBeInTheDocument()
  })

  it('states the terms without needing the server at all', () => {
    stubApi({})
    renderWithQuery(<Terms />)
    expect(screen.getByText(/provided as-is/)).toBeInTheDocument()
    expect(screen.getByText(/Reselling access/)).toBeInTheDocument()
  })
})
