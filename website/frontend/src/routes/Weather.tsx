import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { airQualityLabel, heatAdvisory, weatherGlyph } from '../lib/weather-icons'
import type { WeatherGlyph } from '../lib/weather-icons'
import { BackLink, CapsuleToast, GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, Skeleton } from '../components/ios'
import { CloudIcon, RainIcon, SunCloudIcon, SunIcon } from '../components/icons'
import { ErrorNote } from '../components/ErrorNote'
import { useTheme } from '../lib/theme-context'
import { useDebounced } from '../lib/use-debounced'
import { MAX_WEATHER_PLACES, WEATHER_PLACES_KEY } from '../lib/preferences'
import { formatUnit, unitLabel } from '../lib/units'

type Place = { name: string; country?: string; latitude: number; longitude: number }
type Weather = {
  current: { temperature: number | null; feels_like: number | null; humidity: number | null; wind_speed: number | null; precipitation: number | null; description: string; weather_code: number | null }
  hourly: Array<{ time_local: string; temperature_2m: number; precipitation_probability: number; weather_code: number | null }>
  daily: Array<{ time_local: string; temp_max: number; temp_min: number; feels_like_max: number; feels_like_min: number; precipitation_probability: number; wind_speed_max: number; description: string; weather_code: number | null }>
  air_quality: { european_band: string | null; european_aqi: number | null; us_aqi: number | null; pm10: number | null; pm2_5: number | null; ozone: number | null; nitrogen_dioxide: number | null } | null
  class_suspension: { level: string; reason: string | null } | null
}

// The day cards use the three-glyph set; the hero always uses the sun-behind-cloud
// mark, which is the page's identity rather than a reading of the forecast.
function DayGlyph({ glyph }: { glyph: WeatherGlyph }) {
  if (glyph === 'sun') return <SunIcon />
  if (glyph === 'rain') return <RainIcon />
  return <CloudIcon />
}

// "2026-08-01" -> "Mon". The API sends a local date string, so parsing it as UTC
// and formatting in the viewer's zone could shift it a day; splitting keeps it
// literal.
function dayName(dateLocal: string) {
  const [year, month, day] = dateLocal.split('-').map(Number)
  if (!year || !month || !day) return dateLocal
  return new Date(year, month - 1, day).toLocaleDateString(undefined, { weekday: 'short' })
}

const samePlace = (a: Place, b: Place) => a.latitude === b.latitude && a.longitude === b.longitude

function readSavedPlaces(): Place[] {
  try {
    const items = JSON.parse(localStorage.getItem(WEATHER_PLACES_KEY) ?? '[]')
    return Array.isArray(items) ? items.slice(0, MAX_WEATHER_PLACES) : []
  } catch { return [] }
}

function writeSavedPlaces(places: Place[]) {
  try { localStorage.setItem(WEATHER_PLACES_KEY, JSON.stringify(places)) } catch { /* storage can be unavailable */ }
}

export function Weather() {
  const [query, setQuery] = useState('Iloilo City')
  const [place, setPlace] = useState<Place | null>(null)
  const [saved, setSaved] = useState<Place[]>(readSavedPlaces)
  const [locateError, setLocateError] = useState<string | null>(null)
  const { preferences } = useTheme()
  // Keyed on the debounced value, not the raw one: react-query sees a new key
  // per character otherwise, so the query flips back to pending on every letter
  // and the geocoder is called once per keystroke.
  const search = useDebounced(query)
  const places = useQuery({ queryKey: ['geocode', search], queryFn: () => api<{ results: Place[] }>(`/geocode?q=${encodeURIComponent(search)}`), enabled: search.length >= 2 })
  const weather = useQuery({ queryKey: ['weather', place, preferences.units], queryFn: () => api<Weather>(`/weather?lat=${place!.latitude}&lon=${place!.longitude}&units=${preferences.units}`), enabled: !!place })

  const air = airQualityLabel(weather.data?.air_quality?.european_band)
  const advisory = heatAdvisory(weather.data?.class_suspension?.level)
  const choose = (next: Place) => { setPlace(next); const updated = [next, ...saved.filter(item => !samePlace(item, next))].slice(0, MAX_WEATHER_PLACES); setSaved(updated); writeSavedPlaces(updated) }
  const forget = (target: Place) => { const updated = saved.filter(item => !samePlace(item, target)); setSaved(updated); writeSavedPlaces(updated) }
  const locate = () => {
    setLocateError(null)
    if (!navigator.geolocation) { setLocateError('This browser cannot report your location — search for your city instead.'); return }
    navigator.geolocation.getCurrentPosition(
      position => { setLocateError(null); choose({ name: 'Your location', latitude: position.coords.latitude, longitude: position.coords.longitude }) },
      // Denied and unavailable need different words: one is a setting the
      // person can change, the other is not, and "could not get your location"
      // sends someone hunting through browser settings for a GPS fix that was
      // never going to arrive.
      error => setLocateError(error.code === error.PERMISSION_DENIED
        ? 'Location permission is off for this site — allow it in your browser, or search for your city.'
        : 'Could not get a fix on your location — search for your city instead.'),
      // The default is no timeout at all, so a device that never resolves
      // leaves the button looking inert forever.
      { timeout: 10000, maximumAge: 300000 },
    )
  }

  return <main className="app">
    <LargeTitleHeader title="Weather" subtitle="Search any city for live conditions and the week ahead." />

    <div className="search-field">
      <span className="lens" aria-hidden />
      <input className="search-input" aria-label="Search city" value={query} onChange={event => { setQuery(event.target.value); setPlace(null) }} placeholder="Search a city…" />
    </div>
    {/* The action and the data used to be the same control in the same row, so
        "Use my location" was indistinguishable from a saved city. They are now
        separated, and a saved place is a chip that can be removed. */}
    <div className="weather-tools"><PressableButton variant="secondary" className="small" onClick={locate}>Use my location</PressableButton></div>
    {saved.length > 0 && (
      <ul className="place-chips" aria-label="Saved places">
        {saved.map(item => {
          const active = !!place && samePlace(place, item)
          return <li key={`${item.latitude}:${item.longitude}`} className="place-chip" data-active={active || undefined}>
            <button type="button" className="place-chip-name" aria-current={active || undefined} onClick={() => choose(item)}>{item.name}</button>
            <button type="button" className="place-chip-remove" aria-label={`Remove ${item.name}`} onClick={() => forget(item)}>×</button>
          </li>
        })}
      </ul>
    )}
    {locateError && <div className="stack"><CapsuleToast tone="error">{locateError}</CapsuleToast></div>}

    {!place && (
      <ListGroup>
        {/* Four states, checked in order: pending, error, empty, data. Testing
            `data?.length` first made every keystroke flash "No matching
            places", because the length is falsy while the fetch is in flight. */}
        {search.length < 2
          ? <ListRow label="Type at least two letters" />
          : places.isPending
            ? <ListRow label="Searching…" />
            : places.isError
              ? <ListRow label="Could not search for places"><span className="row-actions"><PressableButton className="small" onClick={() => places.refetch()}>Try again</PressableButton></span></ListRow>
              : places.data?.results?.length
                ? places.data.results.map(result => (
                    <ListRow key={`${result.latitude}:${result.longitude}`} label={`${result.name}${result.country ? `, ${result.country}` : ''}`}>
                      <span className="row-actions"><PressableButton className="small" onClick={() => choose(result)}>Use</PressableButton></span>
                    </ListRow>
                  ))
                : <ListRow label="No matching places" />}
      </ListGroup>
    )}

    {place && weather.isPending && <Skeleton lines={5} />}
    {place && weather.isError && <ErrorNote error={weather.error} onRetry={() => weather.refetch()} />}

    {place && weather.data && <>
      <GlassSurface className="current-weather">
        <div className="current-weather-head">
          <div className="place">
            <h2>{place.name}{place.country && <span className="country">, {place.country}</span>}</h2>
            <p className="current-desc">{weather.data.current.description}</p>
          </div>
          <SunCloudIcon className="hero-icon" />
          <div className="current-temp">{weather.data.current.temperature}<span className="temp-scale">{unitLabel(preferences.units, 'temperature')}</span></div>
        </div>
        <div className="chip-strip">
          {weather.data.current.feels_like !== null && <span className="chip">Feels like {weather.data.current.feels_like}°</span>}
          {weather.data.current.humidity != null && <span className="chip">Humidity {weather.data.current.humidity}%</span>}
          {weather.data.current.wind_speed != null && <span className="chip">Wind {formatUnit(weather.data.current.wind_speed, preferences.units, 'speed')}</span>}
          {weather.data.current.precipitation != null && <span className="chip">Rain {formatUnit(weather.data.current.precipitation, preferences.units, 'length')}</span>}
          {air && <details className="chip air-detail"><summary>Air quality · {air}</summary><div>EU {weather.data.air_quality?.european_aqi ?? '—'} · US {weather.data.air_quality?.us_aqi ?? '—'}<br />PM2.5 {weather.data.air_quality?.pm2_5 ?? '—'} · PM10 {weather.data.air_quality?.pm10 ?? '—'}<br />Ozone {weather.data.air_quality?.ozone ?? '—'} · NO₂ {weather.data.air_quality?.nitrogen_dioxide ?? '—'}</div></details>}
          {/* Surfaced from class_suspension, which the API has always computed and
              the old UI never showed. It is the one thing on this page a reader
              might need to act on today. */}
          {advisory && <span className="chip warn" title={weather.data.class_suspension?.reason ?? undefined}>Heat index advisory</span>}
        </div>
      </GlassSurface>

      {weather.data.hourly.length > 0 && <section className="hourly-strip" aria-label="Hourly forecast">{weather.data.hourly.slice(0, 24).map(hour => (
        // The accessible name carries everything the card shows, including the
        // precipitation figure -- that number used to live in a `title` on an
        // aria-hidden background element, where nobody would ever hear it.
        <div className="hour-card" key={hour.time_local} role="group" aria-label={`${hour.time_local.slice(11, 16)}, ${hour.temperature_2m}${unitLabel(preferences.units, 'temperature')}, ${hour.precipitation_probability}% chance of rain`}>
          <b aria-hidden>{hour.time_local.slice(11, 16)}</b>
          {/* The icon set, not a text emoji: DayGlyph was already in this file,
              forty lines up, doing exactly this for the day cards. Platform
              colour glyphs are off-palette in both themes and different on
              every OS -- DESIGN.md commits to the currentColor set by name. */}
          <span className="hour-glyph" aria-hidden><DayGlyph glyph={weatherGlyph(hour.weather_code)} /></span>
          <strong aria-hidden>{hour.temperature_2m}°</strong>
          {/* A hairline track behind the fill, so the height reads against a
              full-scale reference. On its own a bar means nothing: 40% and 90%
              are just two heights with nothing to compare them to. */}
          <span className="hour-rain" aria-hidden><i style={{ height: `${hour.precipitation_probability}%` }} /></span>
        </div>
      ))}</section>}

      <div className="day-grid">
        {weather.data.daily.map(day => (
          <GlassSurface key={day.time_local} tier="thin" className="day-card">
            <b className="day-name">{dayName(day.time_local)}</b>
            <span className={`day-icon ${weatherGlyph(day.weather_code)}`}><DayGlyph glyph={weatherGlyph(day.weather_code)} /></span>
            <p className="day-temp">{day.temp_max}°<span className="day-low"> / {day.temp_min}°</span></p>
            <small className="day-desc">{day.description}<br />Feels {day.feels_like_min}–{day.feels_like_max}° · {day.precipitation_probability}% rain · wind {formatUnit(day.wind_speed_max, preferences.units, 'speed')}</small>
          </GlassSurface>
        ))}
      </div>
    </>}

    <BackLink to="/">Back home</BackLink>
  </main>
}
