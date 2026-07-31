import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, Skeleton, WidgetGrid } from '../components/ios'

type Place = { name: string; country?: string; latitude: number; longitude: number }
type Weather = {
  current: { temperature: number | null; feels_like: number | null; description: string; icon: string }
  daily: Array<{ time_local: string; temp_max: number; temp_min: number; description: string }>
  air_quality: { european_band: string } | null
}

export function Weather() {
  const [query, setQuery] = useState('Iloilo City')
  const [place, setPlace] = useState<Place | null>(null)
  const places = useQuery({ queryKey: ['geocode', query], queryFn: () => api<{ results: Place[] }>(`/geocode?q=${encodeURIComponent(query)}`), enabled: query.length >= 2 })
  const weather = useQuery({ queryKey: ['weather', place], queryFn: () => api<Weather>(`/weather?lat=${place!.latitude}&lon=${place!.longitude}`), enabled: !!place })

  return <main className="app">
    <LargeTitleHeader title="Weather" subtitle="Search any city for live conditions and the week ahead." />

    <input className="search-input" aria-label="Search city" value={query} onChange={event => { setQuery(event.target.value); setPlace(null) }} placeholder="Search a city…" />

    {!place && (
      <ListGroup>
        {places.data?.results?.length
          ? places.data.results.map(result => (
              <ListRow key={`${result.latitude}:${result.longitude}`} label={`${result.name}${result.country ? `, ${result.country}` : ''}`}>
                <PressableButton onClick={() => setPlace(result)}>Use</PressableButton>
              </ListRow>
            ))
          : <ListRow label={query.length < 2 ? 'Type at least two letters' : 'No matching places'} />}
      </ListGroup>
    )}

    {place && weather.isPending && <Skeleton lines={5} />}

    {place && weather.data && <div className="stack">
      <GlassSurface className="current-weather">
        <div className="current-weather-head">
          <div>
            <h2>{place.name}{place.country ? `, ${place.country}` : ''}</h2>
            <p className="current-desc">{weather.data.current.description}</p>
          </div>
          <div className="current-temp">{weather.data.current.temperature}°</div>
        </div>
        <div className="current-meta">
          <span>Feels like {weather.data.current.feels_like}°</span>
          {weather.data.air_quality && <span>Air quality · {weather.data.air_quality.european_band}</span>}
        </div>
      </GlassSurface>

      <WidgetGrid>
        {weather.data.daily.map(day => (
          <GlassSurface key={day.time_local}>
            <b className="day-name">{day.time_local}</b>
            <p className="stat-value">{day.temp_max}°<span className="day-low"> / {day.temp_min}°</span></p>
            <small className="muted">{day.description}</small>
          </GlassSurface>
        ))}
      </WidgetGrid>
    </div>}

    <p><Link to="/">Back home</Link></p>
  </main>
}
