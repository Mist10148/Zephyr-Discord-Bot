import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { airQualityLabel, heatAdvisory, weatherGlyph } from '../lib/weather-icons'
import type { WeatherGlyph } from '../lib/weather-icons'
import { BackLink, GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, Skeleton } from '../components/ios'
import { CloudIcon, RainIcon, SunCloudIcon, SunIcon } from '../components/icons'
import { useTheme } from '../lib/theme-context'

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

export function Weather() {
  const [query, setQuery] = useState('Iloilo City')
  const [place, setPlace] = useState<Place | null>(null)
  const [saved, setSaved] = useState<Place[]>(() => { try { const items = JSON.parse(localStorage.getItem('zephyr-weather-places') ?? '[]'); return Array.isArray(items) ? items.slice(0, 6) : [] } catch { return [] } })
  const { preferences } = useTheme()
  const places = useQuery({ queryKey: ['geocode', query], queryFn: () => api<{ results: Place[] }>(`/geocode?q=${encodeURIComponent(query)}`), enabled: query.length >= 2 })
  const weather = useQuery({ queryKey: ['weather', place, preferences.units], queryFn: () => api<Weather>(`/weather?lat=${place!.latitude}&lon=${place!.longitude}&units=${preferences.units}`), enabled: !!place })

  const air = airQualityLabel(weather.data?.air_quality?.european_band)
  const advisory = heatAdvisory(weather.data?.class_suspension?.level)
  const choose = (next: Place) => { setPlace(next); const updated = [next, ...saved.filter(item => item.latitude !== next.latitude || item.longitude !== next.longitude)].slice(0, 6); setSaved(updated); try { localStorage.setItem('zephyr-weather-places', JSON.stringify(updated)) } catch { /* ignore */ } }
  const locate = () => navigator.geolocation?.getCurrentPosition(position => choose({ name: 'Your location', latitude: position.coords.latitude, longitude: position.coords.longitude }), () => undefined)

  return <main className="app">
    <LargeTitleHeader title="Weather" subtitle="Search any city for live conditions and the week ahead." />

    <div className="search-field">
      <span className="lens" aria-hidden />
      <input className="search-input" aria-label="Search city" value={query} onChange={event => { setQuery(event.target.value); setPlace(null) }} placeholder="Search a city…" />
    </div>
    <div className="weather-tools"><PressableButton variant="secondary" className="small" onClick={locate}>Use my location</PressableButton>{saved.map(item => <PressableButton key={`${item.latitude}:${item.longitude}`} variant="secondary" className="small" onClick={() => choose(item)}>{item.name}</PressableButton>)}</div>

    {!place && (
      <ListGroup>
        {places.data?.results?.length
          ? places.data.results.map(result => (
              <ListRow key={`${result.latitude}:${result.longitude}`} label={`${result.name}${result.country ? `, ${result.country}` : ''}`}>
                <span className="row-actions"><PressableButton className="small" onClick={() => choose(result)}>Use</PressableButton></span>
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
          {weather.data.current.humidity != null && <span className="chip">Humidity {weather.data.current.humidity}%</span>}
          {weather.data.current.wind_speed != null && <span className="chip">Wind {weather.data.current.wind_speed}</span>}
          {weather.data.current.precipitation != null && <span className="chip">Rain {weather.data.current.precipitation}</span>}
          {air && <details className="chip air-detail"><summary>Air quality · {air}</summary><div>EU {weather.data.air_quality?.european_aqi ?? '—'} · US {weather.data.air_quality?.us_aqi ?? '—'}<br />PM2.5 {weather.data.air_quality?.pm2_5 ?? '—'} · PM10 {weather.data.air_quality?.pm10 ?? '—'}<br />Ozone {weather.data.air_quality?.ozone ?? '—'} · NO₂ {weather.data.air_quality?.nitrogen_dioxide ?? '—'}</div></details>}
          {/* Surfaced from class_suspension, which the API has always computed and
              the old UI never showed. It is the one thing on this page a reader
              might need to act on today. */}
          {advisory && <span className="chip warn" title={weather.data.class_suspension?.reason ?? undefined}>Heat index advisory</span>}
        </div>
      </GlassSurface>

      {weather.data.hourly.length > 0 && <section className="hourly-strip" aria-label="Hourly forecast">{weather.data.hourly.slice(0, 24).map(hour => <div className="hour-card" key={hour.time_local}><b>{hour.time_local.slice(11, 16)}</b><span>{weatherGlyph(hour.weather_code) === 'sun' ? '☀' : weatherGlyph(hour.weather_code) === 'rain' ? '☂' : '☁'}</span><strong>{hour.temperature_2m}°</strong><i style={{ height: `${hour.precipitation_probability}%` }} title={`${hour.precipitation_probability}% precipitation`} /></div>)}</section>}

      <div className="day-grid">
        {weather.data.daily.map(day => (
          <GlassSurface key={day.time_local} tier="thin" className="day-card">
            <b className="day-name">{dayName(day.time_local)}</b>
            <span className={`day-icon ${weatherGlyph(day.weather_code)}`}><DayGlyph glyph={weatherGlyph(day.weather_code)} /></span>
            <p className="day-temp">{day.temp_max}°<span className="day-low"> / {day.temp_min}°</span></p>
            <small className="day-desc">{day.description}<br />Feels {day.feels_like_min}–{day.feels_like_max}° · {day.precipitation_probability}% rain · wind {day.wind_speed_max}</small>
          </GlassSurface>
        ))}
      </div>
    </>}

    <BackLink to="/">Back home</BackLink>
  </main>
}
