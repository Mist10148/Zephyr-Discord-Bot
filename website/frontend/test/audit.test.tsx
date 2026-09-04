import { describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { GuildAudit } from '../src/routes/GuildAudit'
import { renderWithQuery, stubApi } from './helpers'

const entry = (id: number, action: string, source = 'web') => ({
  id, guild_id: '1', actor_id: '900000000000000001', action,
  payload: null, source, created_at: '2026-09-04T10:00:00+00:00',
})

function stubAudit() {
  const calls: string[] = []
  stubApi({
    '/guilds/1/audit': url => {
      calls.push(url.search)
      const action = url.searchParams.get('action')
      const all = [entry(3, 'player.volume'), entry(2, 'settings.update'), entry(1, 'ai.memory.purge', 'discord')]
      const entries = action ? all.filter(item => item.action.startsWith(action)) : all
      return { body: { id: '1', entries, next_before: null, actors: {} } }
    },
    '/guilds/1': { body: { id: '1', name: 'G', icon_url: null, owner: true, bot_present: true, locale: 'en', timezone: 'UTC', default_volume: 50, dj_role_id: null, music_channel_ids: [], enabled_cogs: [], defaults_applied: false, member_count: 1, bot_snapshot_at: null } },
  })
  return calls
}

const render = () => renderWithQuery(<GuildAudit />, { route: '/g/1/audit', path: '/g/:guildId/audit' })

describe('audit filters', () => {
  it('sends the filter to the server, not to the loaded page', async () => {
    const calls = stubAudit()
    render()
    // Scoped to the list: GuildShell's nav also has a "Settings" link, so a
    // bare text query matches the navigation as well as the rows.
    const rows = () => (document.querySelector('.audit-group') as HTMLElement | null)
    // The rows show ACTION_LABELS text, not the raw action names.
    await waitFor(() => expect(rows()?.textContent).toContain('Player: volume'))
    expect(rows()?.textContent).toContain('Updated server settings')

    fireEvent.change(screen.getByLabelText('Action'), { target: { value: 'player' } })

    // The whole point of F4: the page is keyset-paginated, so a client-side
    // filter could only narrow the rows already fetched.
    await waitFor(() => expect(calls.some(search => search.includes('action=player'))).toBe(true))
    await waitFor(() => expect(rows()?.textContent).not.toContain('Updated server settings'))
    expect(rows()?.textContent).toContain('Player: volume')
  })

  it('combines action and source', async () => {
    const calls = stubAudit()
    render()
    fireEvent.change(await screen.findByLabelText('Action'), { target: { value: 'ai' } })
    fireEvent.change(screen.getByLabelText('From'), { target: { value: 'discord' } })

    await waitFor(() => expect(calls.some(s => s.includes('action=ai') && s.includes('source=discord'))).toBe(true))
  })

  it('distinguishes filtered-to-nothing from an empty log', async () => {
    stubApi({
      '/guilds/1/audit': url => ({
        body: { id: '1', entries: url.searchParams.get('action') ? [] : [entry(1, 'player.volume')], next_before: null, actors: {} },
      }),
      '/guilds/1': { body: { id: '1', name: 'G', icon_url: null, owner: true, bot_present: true, locale: 'en', timezone: 'UTC', default_volume: 50, dj_role_id: null, music_channel_ids: [], enabled_cogs: [], defaults_applied: false, member_count: 1, bot_snapshot_at: null } },
    })
    render()
    await waitFor(() => expect(screen.getByLabelText('Action')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Action'), { target: { value: 'settings' } })
    // The two states look identical otherwise, and one of them means "change
    // the filter".
    await waitFor(() => expect(screen.getByText(/Nothing matches those filters/)).toBeInTheDocument())
    expect(screen.queryByText(/Nothing has been changed here yet/)).not.toBeInTheDocument()
  })

  it('offers a way back to everything', async () => {
    stubAudit()
    render()
    fireEvent.change(await screen.findByLabelText('Action'), { target: { value: 'player' } })

    const clear = await screen.findByRole('button', { name: 'Clear' })
    fireEvent.click(clear)
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Clear' })).not.toBeInTheDocument())
  })
})
