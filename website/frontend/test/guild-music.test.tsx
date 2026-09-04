import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { GuildMusic } from '../src/routes/GuildMusic'
import { ThemeProvider } from '../src/lib/theme'
import { renderWithQuery, stubApi } from './helpers'
import { resetToasts } from '../src/lib/toast'
import { ToastHost } from '../src/components/ToastHost'
import type { Player, PlayerTrack } from '../src/types/api'

const track = (title: string, url: string): PlayerTrack => ({
  title, url, duration_s: 210, requester_id: '1', requester_mention: '<@1>',
  uploader: 'Uploader', thumbnail: null, source: 'youtube',
})

const PLAYER: Player = {
  guild_id: '1', live: true, connected: true, playing: true, paused: false,
  position_s: 30, duration_s: 210, loop: 'off', volume: 50, autoplay: false,
  effects: { bass_boost: 0, pitch: 1, nightcore: false, vaporwave: false, reverb: false, slowed: false, slownrev: false, sixteen_d: false },
  track: track('Now Playing', 'https://y.tld/now'),
  queue: [track('Second', 'https://y.tld/second'), track('Third', 'https://y.tld/third')],
  queue_length: 2, queue_duration_s: 420,
}

/** Answers the player poll, records every player POST, and lets a spec inspect them. */
function stubPlayer(player: Player = PLAYER) {
  const posts: Array<{ action: string; body: unknown }> = []
  stubApi({
    '/guilds/1/player': (url, init) => {
      if (init?.method && init.method !== 'GET') {
        posts.push({ action: url.pathname.split('/').pop()!, body: JSON.parse(String(init.body ?? '{}')) })
        return { status: 204 }
      }
      return { body: player }
    },
    '/guilds/1/meta': { body: { channels: [], roles: [], voice_channels: [] } },
    '/playlists': { body: { playlists: [] } },
    '/guilds/1': { body: { id: '1', name: 'Guild', prefix: '/', enabled_cogs: [], music_channel_ids: [] } },
  })
  return posts
}

afterEach(() => resetToasts())

// GuildMusic has its own aria-live now-playing announcer, which carries an
// implicit role=status -- so a bare getByRole('status') finds that empty div
// rather than a toast. Every toast assertion goes through the region.
const toastRegion = () => within(document.querySelector('.toast-region') as HTMLElement)

const render = () =>
  renderWithQuery(<ThemeProvider><GuildMusic /><ToastHost /></ThemeProvider>, { route: '/g/1/music', path: '/g/:guildId/music' })

// GuildMusic reads :guildId from the router, so the route has to carry it. The
// component is rendered directly rather than through App's route table, which
// would drag in RequireAuth and the whole shell.
describe('the live queue', () => {
  it('has no inert button, and the label matches what it does', async () => {
    const posts = stubPlayer()
    render()
    await waitFor(() => expect(screen.getByText('Second')).toBeInTheDocument())

    // "Play" promised an absolute jump the bridge cannot perform from here.
    expect(screen.queryByRole('button', { name: 'Play' })).not.toBeInTheDocument()
    // Each row's accessible name names its track. The visible text is the same
    // on every row, and the search field already has a "Play next" toggle, so
    // the bare label would make three different controls indistinguishable.
    const second = screen.getByRole('button', { name: 'Play Second next' })
    expect(screen.getByRole('button', { name: 'Play Third next' })).toBeInTheDocument()

    fireEvent.click(second)
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toEqual({ action: 'play', body: { query: 'https://y.tld/second', mode: 'next' } })
  })
})

describe('the effects sliders', () => {
  it('send one request per drag, not one per step', async () => {
    const posts = stubPlayer()
    render()
    fireEvent.click(await screen.findByText('Audio effects'))
    const pitch = await screen.findByLabelText('Pitch')

    // Dragging 1.0 -> 1.5 at step .1 is five onChange events. Straight from
    // onChange that was five mutations, enough to feel like mud and to eat most
    // of PLAYER_RATE_LIMIT in one gesture.
    for (const value of ['1.1', '1.2', '1.3', '1.4', '1.5']) {
      fireEvent.change(pitch, { target: { value } })
    }
    expect(posts).toHaveLength(0)

    fireEvent.pointerUp(pitch)
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toEqual({ action: 'effects', body: { pitch: 1.5 } })
  })

  it('commits from the keyboard too', async () => {
    const posts = stubPlayer()
    render()
    fireEvent.click(await screen.findByText('Audio effects'))
    const bass = await screen.findByLabelText('Bass boost')

    fireEvent.change(bass, { target: { value: '6' } })
    fireEvent.keyUp(bass, { key: 'ArrowRight' })
    await waitFor(() => expect(posts).toEqual([{ action: 'effects', body: { bass_boost: 6 } }]))
  })

  it('shows its current value the way the volume row does', async () => {
    stubPlayer()
    render()
    fireEvent.click(await screen.findByText('Audio effects'))

    // The volume row has always shown "50%"; these showed nothing at all.
    expect(screen.getByText('50%')).toBeInTheDocument()
    expect(screen.getByText('+0 dB')).toBeInTheDocument()
    expect(screen.getByText('1.0x')).toBeInTheDocument()

    const pitch = screen.getByLabelText('Pitch')
    fireEvent.change(pitch, { target: { value: '1.7' } })
    // The draft wins while dragging, so the readout tracks the thumb rather
    // than waiting for the 3s poll to come back.
    await waitFor(() => expect(screen.getByText('1.7x')).toBeInTheDocument())
  })
})

describe('feedback', () => {
  it('confirms a queued track instead of waiting for the 3s poll', async () => {
    stubPlayer()
    render()
    const input = await screen.findByLabelText('Song or URL to queue')
    fireEvent.change(input, { target: { value: 'bohemian rhapsody' } })
    fireEvent.click(within(document.querySelector('.music-search') as HTMLElement).getByRole('button', { name: 'Queue' }))

    await waitFor(() => expect(toastRegion().getByRole('status')).toHaveTextContent('Queued'))
    // The input still clears -- that behaviour was never the problem.
    expect((input as HTMLInputElement).value).toBe('')
  })

  it('announces a refusal without moving the page', async () => {
    const posts: Array<{ action: string }> = []
    stubApi({
      '/guilds/1/player': (url, init) => {
        if (init?.method && init.method !== 'GET') {
          posts.push({ action: url.pathname.split('/').pop()! })
          return { status: 409, body: { error: { code: 'refused', message: 'Nothing is playing.' } } }
        }
        return { body: PLAYER }
      },
      '/guilds/1/meta': { body: { channels: [], roles: [], voice_channels: [] } },
      '/playlists': { body: { playlists: [] } },
      '/guilds/1': { body: { id: '1', name: 'Guild', prefix: '/', enabled_cogs: [], music_channel_ids: [] } },
    })
    render()
    fireEvent.click(await screen.findByRole('button', { name: 'Skip' }))

    // The message comes from the Flask envelope, through the global
    // MutationCache in lib/query.ts -- no per-call-site wiring.
    const alert = await waitFor(() => toastRegion().getByRole('alert'))
    expect(alert).toHaveTextContent('Nothing is playing.')
    // It must be in the fixed region, not injected between the effects panel
    // and the queue heading the way the old inline ErrorNote was.
    expect(alert.closest('.toast-region')).not.toBeNull()
    expect(document.querySelector('main.app .toast')).toBeNull()
  })

  it('puts the undo where it can actually be reached', async () => {
    const posts = stubPlayer()
    render()
    await waitFor(() => expect(screen.getByText('Second')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Remove Second' }))

    const toast = await waitFor(() => toastRegion().getByRole('status'))
    expect(toast).toHaveTextContent('Removed Second')
    // Previously a hand-rolled div rendered *after* the queue list, so on a long
    // queue it lived its whole life below the fold.
    expect(toast.closest('.toast-region')).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(posts.map(post => post.action)).toEqual(['remove', 'play']))
    expect(posts[1].body).toEqual({ query: 'https://y.tld/second', mode: 'next' })
  })
})

describe('display vocabulary', () => {
  it('names every effect instead of showing its identifier', async () => {
    stubPlayer()
    render()
    fireEvent.click(await screen.findByText('Audio effects'))

    // `effect.replace('_', ' ')` is non-global, so these read as "sixteen d"
    // and "slownrev" -- and none of them is guessable even with the underscore
    // handled, hence the one-line detail per row.
    expect(screen.getByText('16D Audio')).toBeInTheDocument()
    expect(screen.getByText('Slowed + Reverb')).toBeInTheDocument()
    expect(screen.getByText('Pans the track around your head')).toBeInTheDocument()

    for (const identifier of ['sixteen d', 'sixteen_d', 'slownrev', 'nightcore']) {
      expect(screen.queryByText(identifier)).not.toBeInTheDocument()
    }
  })

  it('draws an icon where the track art would be, not the words "track art"', async () => {
    stubPlayer({ ...PLAYER, track: { ...PLAYER.track!, thumbnail: null } })
    render()
    await waitFor(() => expect(screen.getByText('Now Playing')).toBeInTheDocument())

    const placeholder = document.querySelector('.art-placeholder')!
    expect(placeholder.textContent).toBe('')
    expect(placeholder.querySelector('svg')).not.toBeNull()
  })
})
