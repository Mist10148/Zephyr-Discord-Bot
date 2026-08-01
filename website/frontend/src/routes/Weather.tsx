import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { airQualityLabel, heatAdvisory, weatherGlyph } from '../lib/weather-icons'
import type { WeatherGlyph } from '../lib/weather-icons'
import { BackLink, GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, Skeleton } from '../components/ios'
import { CloudIcon, RainIcon, SunCloudIcon, SunIcon } from '../components/icons'

type Place = { name: string; country?: string; latitude: number; longitude: number }
type Weather = {
  current: { temperature: number | null; feels_like: number | null; description: string; weather_code: number | null }
  daily: Array<{ time_local: string; temp_max: number; temp_min: number; description: string; weather_code: number | null }>
  air_quality: { european_band: string | null } | null
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

export function Weather() {
  const [query, setQuery] = useState('Iloilo City')
  const [place, setPlace] = useState<Place | null>(null)
  const places = useQuery({ queryKey: ['geocode', query], queryFn: () => api<{ results: Place[] }>(`/geocode?q=${encodeURIComponent(query)}`), enabled: query.length >= 2 })
  const weather = useQuery({ queryKey: ['weather', place], queryFn: () => api<Weather>(`/weather?lat=${place!.latitude}&lon=${place!.longitude}`), enabled: !!place })

  const air = airQualityLabel(weather.data?.air_quality?.european_band)
  const advisory = heatAdvisory(weather.data?.class_suspension?.level)

  return <main className="app">
    <LargeTitleHeader title="Weather" subtitle="Search any city for live conditions and the week ahead." />

    <div className="search-field">
      <span className="lens" aria-hidden />
      <input className="search-input" aria-label="Search city" value={query} onChange={event => { setQuery(event.target.value); setPlace(null) }} placeholder="Search a city…" />
    </div>

    {!place && (
      <ListGroup>
        {places.data?.results?.length
          ? places.data.results.map(result => (
              <ListRow key={`${result.latitude}:${result.longitude}`} label={`${result.name}${result.country ? `, ${result.country}` : ''}`}>
                <span className="row-actions"><PressableButton className="small" onClick={() => setPlace(result)}>Use</PressableButton></span>
              </ListRow>
            ))
          : <ListRow label={query.length < 2 ? 'Type at least two letters' : 'No matching places'} />}
      </ListGroup>
    )}

    {place && weather.isPending && <Skeleton lines={5} />}

    {place && weather.data && <>
      <GlassSurface className="current-weather">
        <div className="current-weather-head">
          <div className="place">
            <h2>{place.name}{place.country && <span className="country">, {place.country}</span>}</h2>
            <p className="current-desc">{weather.data.current.description}</p>
          </div>
          <SunCloudIcon className="hero-icon" />
          <div className="current-temp">{weather.data.current.temperature}°</div>
        </div>
        <div className="chip-strip">
          {weather.data.current.feels_like !== null && <span className="chip">Feels like {weather.data.current.feels_like}°</span>}
          {air && <span className="chip">Air quality · {air}</span>}
          {/* Surfaced from class_suspension, which the API has always computed and
              the old UI never showed. It is the one thing on this page a reader
              might need to act on today. */}
          {advisory && <span className="chip warn" title={weather.data.class_suspension?.reason ?? undefined}>Heat index advisory</span>}
        </div>
      </GlassSurface>

      <div className="day-grid">
        {weather.data.daily.map(day => (
          <GlassSurface key={day.time_local} tier="thin" className="day-card">
            <b className="day-name">{dayName(day.time_local)}</b>
            <span className={`day-icon ${weatherGlyph(day.weather_code)}`}><DayGlyph glyph={weatherGlyph(day.weather_code)} /></span>
            <p className="day-temp">{day.temp_max}°<span className="day-low"> / {day.temp_min}°</span></p>
            <small className="day-desc">{day.description}</small>
          </GlassSurface>
        ))}
      </div>
    </>}

    <BackLink to="/">Back home</BackLink>
  </main>
}
