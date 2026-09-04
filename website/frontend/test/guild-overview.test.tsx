import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { GuildOverview } from '../src/routes/GuildOverview'
import { renderWithQuery, stubApi } from './helpers'

const GUILD = {
  id: '1', name: 'Zephyr Test', icon_url: null, owner: true, bot_present: true,
  locale: 'en', timezone: 'Asia/Manila', default_volume: 50,
  dj_role_id: '555000000000000001',
  music_channel_ids: ['777000000000000001', '777000000000000002'],
  enabled_cogs: ['music', 'weather'],
  defaults_applied: false, member_count: 12, bot_snapshot_at: null,
}

const META = {
  channels: [
    { id: '777000000000000001', name: 'music', can_send: true },
    { id: '777000000000000002', name: 'bot-spam', can_send: true },
  ],
  roles: [{ id: '555000000000000001', name: 'DJ', managed: false }],
  voice_channels: [],
}

const COMMANDS = { categories: [{ key: 'music', title: 'Music' }, { key: 'weather', title: 'Weather' }] }

const render = () =>
  renderWithQuery(<GuildOverview />, { route: '/g/1', path: '/g/:guildId' })

describe('the configuration list', () => {
  it('shows names, not snowflakes', async () => {
    stubApi({ '/guilds/1/meta': { body: META }, '/commands': { body: COMMANDS }, '/guilds/1': { body: GUILD } })
    render()

    await waitFor(() => expect(screen.getByText('DJ')).toBeInTheDocument())
    expect(screen.getByText('#music')).toBeInTheDocument()
    expect(screen.getByText('#bot-spam')).toBeInTheDocument()
    // Raw Python module names were rendered for the modules row.
    expect(screen.getByText('Music · Weather')).toBeInTheDocument()

    for (const id of ['555000000000000001', '777000000000000001']) {
      expect(screen.queryByText(id)).not.toBeInTheDocument()
    }
  })

  it('degrades to ids, marked as such, when the bot is unreachable', async () => {
    stubApi({
      '/guilds/1/meta': { status: 503, body: { error: { code: 'bridge_unavailable', message: 'Could not reach Zephyr.' } } },
      '/commands': { body: COMMANDS },
      '/guilds/1': { body: GUILD },
    })
    render()

    // The id stands, which is worse than a name and far better than an error
    // page -- but it has to read as a fallback rather than as a chosen value.
    const raw = await waitFor(() => screen.getByText('555000000000000001'))
    expect(raw).toHaveClass('faint')
    expect(raw).toHaveAttribute('title', expect.stringContaining('not reachable'))
  })

  it('still names the modules when only the bridge is down', async () => {
    stubApi({
      '/guilds/1/meta': { status: 503, body: { error: { code: 'x', message: 'down' } } },
      '/commands': { body: COMMANDS },
      '/guilds/1': { body: GUILD },
    })
    render()
    // /commands is unauthenticated and does not go through the bot at all.
    await waitFor(() => expect(screen.getByText('Music · Weather')).toBeInTheDocument())
  })
})
