import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { Weather } from '../src/routes/Weather'
import { ThemeProvider } from '../src/lib/theme'
import { WEATHER_PLACES_KEY } from '../src/lib/preferences'
import { renderWithQuery, stubApi } from './helpers'

const PLACE = { name: 'Iloilo City', country: 'Philippines', latitude: 10.7, longitude: 122.5 }

const FORECAST = {
  current: { temperature: 31, feels_like: 38, humidity: 74, wind_speed: 12, precipitation: 0.4, description: 'Partly cloudy', weather_code: 2 },
  hourly: [{ time_local: '2026-09-04T14:00', temperature_2m: 31, precipitation_probability: 40, weather_code: 2 }],
  daily: [{ time_local: '2026-09-04', temp_max: 32, temp_min: 25, feels_like_max: 39, feels_like_min: 26, precipitation_probability: 40, wind_speed_max: 18, description: 'Partly cloudy', weather_code: 2 }],
  air_quality: null,
  class_suspension: null,
}

const render = () => renderWithQuery(<ThemeProvider><Weather /></ThemeProvider>)

const typeCity = (value: string) =>
  fireEvent.change(screen.getByLabelText('Search city'), { target: { value } })

describe('Weather search', () => {
  it('goes loading -> results and never shows a false empty state', async () => {
    stubApi({ '/geocode': { body: { results: [PLACE] } } })
    render()
    // The initial query is 'Iloilo City' -- over two characters, so the very
    // first paint is the pending state. Testing `data?.results?.length` first
    // rendered "No matching places" here, because the length is falsy while
    // the fetch is in flight.
    expect(screen.getByText('Searching…')).toBeInTheDocument()
    expect(screen.queryByText('No matching places')).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Iloilo City, Philippines')).toBeInTheDocument())
    expect(screen.queryByText('Searching…')).not.toBeInTheDocument()
  })

  it('asks the geocoder once per pause, not once per character', async () => {
    const { calls } = stubApi({ '/geocode': { body: { results: [] } } })
    render()

    // Each keystroke has to be its own committed render, which is the whole
    // difficulty of this spec. Four synchronous fireEvent.change calls get
    // batched into a single commit, so react-query only ever observes the final
    // key -- and the undebounced version passes too, for the wrong reason.
    // Yielding between them models a person typing.
    for (const value of ['Man', 'Mani', 'Manil', 'Manila']) {
      typeCity(value)
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    }

    await waitFor(() => expect(calls).toContain('/geocode?q=Manila'))
    // Assert on the exact list, not on substrings: '/geocode?q=Manila'
    // *contains* neither 'q=Man&' nor 'Man' as a whole query, so a substring
    // filter cannot tell one request from four and passes either way.
    const geocodes = calls.filter(path => path.startsWith('/geocode'))
    expect(geocodes).toEqual(['/geocode?q=Iloilo%20City', '/geocode?q=Manila'])
  })

  it('offers a retry when the geocoder fails', async () => {
    stubApi({ '/geocode': { status: 500, body: { error: { code: 'upstream', message: 'Geocoder down' } } } })
    render()
    await waitFor(() => expect(screen.getByText('Could not search for places')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('distinguishes two letters from no matches', async () => {
    stubApi({ '/geocode': { body: { results: [] } } })
    render()
    typeCity('I')
    await waitFor(() => expect(screen.getByText('Type at least two letters')).toBeInTheDocument())
  })
})

describe('Weather forecast', () => {
  it('shows a message and a working retry when /weather fails', async () => {
    let attempts = 0
    stubApi({
      '/geocode': { body: { results: [PLACE] } },
      '/weather': () => {
        attempts += 1
        return attempts === 1
          ? { status: 500, body: { error: { code: 'upstream', message: 'Open-Meteo is unreachable' } } }
          : { body: FORECAST }
      },
    })
    render()
    fireEvent.click(await screen.findByRole('button', { name: 'Use' }))

    await waitFor(() => expect(screen.getByText(/Open-Meteo is unreachable/)).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    await waitFor(() => expect(screen.getByText('Partly cloudy')).toBeInTheDocument())
  })
})

describe('Use my location', () => {
  it('says so when permission is denied', async () => {
    stubApi({ '/geocode': { body: { results: [] } } })
    vi.mocked(navigator.geolocation.getCurrentPosition).mockImplementation((_ok, fail) =>
      fail?.({ code: 1, PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3, message: '' } as GeolocationPositionError),
    )
    render()
    fireEvent.click(screen.getByRole('button', { name: 'Use my location' }))
    await waitFor(() => expect(screen.getByText(/Location permission is off/)).toBeInTheDocument())
  })

  it('says something different when there is simply no fix', async () => {
    stubApi({ '/geocode': { body: { results: [] } } })
    vi.mocked(navigator.geolocation.getCurrentPosition).mockImplementation((_ok, fail) =>
      fail?.({ code: 3, PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3, message: '' } as GeolocationPositionError),
    )
    render()
    fireEvent.click(screen.getByRole('button', { name: 'Use my location' }))
    await waitFor(() => expect(screen.getByText(/Could not get a fix/)).toBeInTheDocument())
  })

  it('passes a timeout, because the default is none at all', async () => {
    stubApi({ '/geocode': { body: { results: [] } } })
    render()
    fireEvent.click(screen.getByRole('button', { name: 'Use my location' }))
    const options = vi.mocked(navigator.geolocation.getCurrentPosition).mock.calls[0]?.[2]
    expect(options?.timeout).toBe(10000)
  })
})

describe('Saved places', () => {
  it('can be removed, and the removal survives a remount', async () => {
    localStorage.setItem(WEATHER_PLACES_KEY, JSON.stringify([PLACE, { name: 'Cebu', latitude: 10.3, longitude: 123.9 }]))
    stubApi({ '/geocode': { body: { results: [] } }, '/weather': { body: FORECAST } })
    const first = render()
    expect(screen.getByRole('button', { name: 'Cebu' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Remove Cebu' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Cebu' })).not.toBeInTheDocument())
    expect(JSON.parse(localStorage.getItem(WEATHER_PLACES_KEY)!)).toHaveLength(1)

    first.unmount()
    render()
    expect(screen.queryByRole('button', { name: 'Cebu' })).not.toBeInTheDocument()
  })

  it('marks the place currently on screen', async () => {
    localStorage.setItem(WEATHER_PLACES_KEY, JSON.stringify([PLACE]))
    stubApi({ '/geocode': { body: { results: [] } }, '/weather': { body: FORECAST } })
    render()
    const chip = screen.getByRole('button', { name: 'Iloilo City' })
    expect(chip).not.toHaveAttribute('aria-current')

    fireEvent.click(chip)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Iloilo City' })).toHaveAttribute('aria-current', 'true'),
    )
  })

  it('keeps the action visually separate from the data', () => {
    localStorage.setItem(WEATHER_PLACES_KEY, JSON.stringify([PLACE]))
    stubApi({ '/geocode': { body: { results: [] } } })
    render()
    // The defect was both being a PressableButton in one row. The action keeps
    // .ios-button; a saved place must not have it.
    expect(screen.getByRole('button', { name: 'Use my location' }).className).toContain('ios-button')
    expect(screen.getByRole('button', { name: 'Iloilo City' }).className).not.toContain('ios-button')
  })
})

describe('display vocabulary', () => {
  it('never renders a dimensioned number without its unit', async () => {
    stubApi({ '/geocode': { body: { results: [PLACE] } }, '/weather': { body: FORECAST } })
    render()
    fireEvent.click(await screen.findByRole('button', { name: 'Use' }))
    await waitFor(() => expect(screen.getByText('Partly cloudy')).toBeInTheDocument())

    // "Wind 12" was genuinely ambiguous once C8 added the units preference.
    expect(screen.getByText(/Wind 12 km\/h/)).toBeInTheDocument()
    expect(screen.getByText(/Rain 0.4 mm/)).toBeInTheDocument()
    expect(screen.getByText(/wind 18 km\/h/)).toBeInTheDocument()
    // The page declares its scale once, on the hero reading.
    expect(screen.getByText('°C')).toBeInTheDocument()
  })

  it('draws the hourly strip with the icon set, not text emoji', async () => {
    stubApi({ '/geocode': { body: { results: [PLACE] } }, '/weather': { body: FORECAST } })
    render()
    fireEvent.click(await screen.findByRole('button', { name: 'Use' }))
    await waitFor(() => expect(document.querySelector('.hourly-strip')).not.toBeNull())

    // DESIGN.md names ☀ ☂ ☁ specifically: platform colour glyphs are
    // off-palette in both themes and different on every OS.
    const strip = document.querySelector('.hourly-strip')!
    expect(strip.textContent).not.toMatch(/[☀☂☁]/)
    expect(strip.querySelectorAll('.hour-glyph svg').length).toBeGreaterThan(0)
  })

  it('gives each hour cell an accessible name carrying its rain figure', async () => {
    stubApi({ '/geocode': { body: { results: [PLACE] } }, '/weather': { body: FORECAST } })
    render()
    fireEvent.click(await screen.findByRole('button', { name: 'Use' }))

    // The number used to sit in a `title` on an aria-hidden background element.
    const cell = await waitFor(() => screen.getByRole('group', { name: /14:00, 31°C, 40% chance of rain/ }))
    expect(cell).toBeInTheDocument()
    // The bar now has a full-scale track behind it, so its height means something.
    expect(cell.querySelector('.hour-rain i')).not.toBeNull()
  })
})
