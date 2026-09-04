import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { AppShell } from '../src/components/AppShell'
import { TabBar } from '../src/components/TabBar'
import { Commands } from '../src/routes/Commands'
import { Home } from '../src/routes/Home'
import { ToastHost } from '../src/components/ToastHost'
import { resetToasts } from '../src/lib/toast'
import { ThemeProvider } from '../src/lib/theme'
import { renderWithQuery, stubApi } from './helpers'

afterEach(() => resetToasts())

const COMMANDS = {
  commands: [
    { name: 'play', aliases: ['p'], args: [{ name: 'query', required: true }], description: 'Queue a track', category: 'music', category_title: 'Music', emoji: '🎵' },
    { name: 'skip', aliases: [], args: [], description: 'Skip the current track', category: 'music', category_title: 'Music', emoji: '🎵' },
    { name: 'weather', aliases: ['w'], args: [{ name: 'city', required: false }], description: 'Current conditions', category: 'weather', category_title: 'Weather', emoji: '🌦️' },
  ],
  categories: [{ key: 'music', title: 'Music', emoji: '🎵' }, { key: 'weather', title: 'Weather', emoji: '🌦️' }],
}

describe('reaching /commands', () => {
  it('is in the top-bar nav', () => {
    renderWithQuery(<ThemeProvider><AppShell onOpenPalette={() => undefined}>content</AppShell></ThemeProvider>)
    // Previously reachable only from the ⌘K palette.
    expect(screen.getByRole('link', { name: 'Commands' })).toHaveAttribute('href', '/commands')
  })

  it('is a card on the landing page, in place of the design system', async () => {
    stubApi({ '/status': { body: { bot: { online: true } } } })
    renderWithQuery(<Home />)
    await waitFor(() => expect(screen.getByRole('link', { name: /Commands/ })).toBeInTheDocument())
    // Selling an internal review surface as one of three headline features was
    // the defect; the route stays, as a link from /settings.
    expect(screen.queryByRole('link', { name: /Design system/ })).not.toBeInTheDocument()
  })
})

describe('one destination, one name', () => {
  it('calls /settings "Appearance" in both navigations', () => {
    const { unmount } = renderWithQuery(<ThemeProvider><AppShell onOpenPalette={() => undefined}>content</AppShell></ThemeProvider>)
    expect(screen.getByRole('link', { name: 'Appearance' })).toHaveAttribute('href', '/settings')
    unmount()

    renderWithQuery(<TabBar />)
    // The tab bar said "System" for the same page.
    expect(screen.getByRole('link', { name: 'Appearance' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'System' })).not.toBeInTheDocument()
  })
})

describe('the command reference', () => {
  const render = () => renderWithQuery(<><Commands /><ToastHost /></>)

  it('offers a category jump list and counts the results', async () => {
    stubApi({ '/commands': { body: COMMANDS } })
    render()
    await waitFor(() => expect(screen.getByText('3 commands')).toBeInTheDocument())

    const jump = screen.getByRole('navigation', { name: 'Jump to a category' })
    expect(within(jump).getByRole('link', { name: /Music/ })).toHaveAttribute('href', '#commands-music')
    expect(within(jump).getByRole('link', { name: /Weather/ })).toHaveAttribute('href', '#commands-weather')
  })

  it('says how many of how many while filtering', async () => {
    stubApi({ '/commands': { body: COMMANDS } })
    render()
    await waitFor(() => expect(screen.getByText('3 commands')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Search commands'), { target: { value: 'weather' } })
    await waitFor(() => expect(screen.getByText('1 of 3 commands')).toBeInTheDocument())
    // A chip must never scroll to a section the filter removed.
    const jump = screen.queryByRole('navigation', { name: 'Jump to a category' })
    expect(jump && within(jump).queryByRole('link', { name: /Music/ })).toBeFalsy()
  })

  it('shows the alias that caused a match', async () => {
    stubApi({ '/commands': { body: COMMANDS } })
    render()
    // Aliases were already a Fuse search key and were never rendered, so
    // searching one returned a hit with no visible reason for the match.
    await waitFor(() => expect(screen.getByText(/also \/p/)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Search commands'), { target: { value: 'p' } })
    await waitFor(() => expect(screen.getByText('/play')).toBeInTheDocument())
  })

  it('copies a command, and says so', async () => {
    stubApi({ '/commands': { body: COMMANDS } })
    render()
    fireEvent.click(await screen.findByRole('button', { name: 'Copy /play' }))

    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('/play'))
    const region = within(document.querySelector('.toast-region') as HTMLElement)
    await waitFor(() => expect(region.getByRole('status')).toHaveTextContent('Copied /play'))
  })

  it('reports a clipboard the browser refused', async () => {
    stubApi({ '/commands': { body: COMMANDS } })
    vi.mocked(navigator.clipboard.writeText).mockRejectedValueOnce(new Error('denied'))
    render()
    fireEvent.click(await screen.findByRole('button', { name: 'Copy /play' }))

    const region = within(document.querySelector('.toast-region') as HTMLElement)
    await waitFor(() => expect(region.getByRole('alert')).toHaveTextContent('Could not copy'))
  })
})
