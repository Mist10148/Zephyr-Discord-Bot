import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { PressableButton } from '../components/ios'

const FEATURES = [
  { to: '/weather', title: 'Weather', body: 'Live conditions, a daily forecast, and heat-index advisories for any city.' },
  { to: '/g', title: 'Dashboard', body: 'Sign in with Discord to manage music, alerts, AI and settings per server.' },
  { to: '/kitchen-sink', title: 'Design system', body: 'The glass primitives this whole interface is built from.' },
]

export function Home() {
  const status = useQuery({ queryKey: ['status'], queryFn: () => api<{ bot: { online: boolean } }>('/status') })
  const navigate = useNavigate()
  const online = status.data?.bot.online
  const statusClass = status.isPending ? '' : online ? 'ok' : 'off'
  const statusText = status.isPending ? 'Connecting…' : online ? 'Bot online' : 'Bot offline'

  return <main className="app">
    <section className="hero">
      <span className="hero-mark" aria-hidden>❍</span>
      <h1>Zephyr</h1>
      <p className="hero-tagline">A weather-first Discord companion — forecasts, music and AI, with a dashboard to match.</p>
      <span className={`status-pill ${statusClass}`} role="status"><i aria-hidden />{statusText}</span>
      <div className="hero-actions">
        <PressableButton onClick={() => navigate('/weather')}>Check the weather</PressableButton>
        <PressableButton variant="secondary" onClick={() => navigate('/g')}>Open dashboard</PressableButton>
      </div>
    </section>

    <div className="feature-grid">
      {FEATURES.map(feature => (
        <Link key={feature.to} to={feature.to} className="glass glass-interactive feature-card">
          <h2>{feature.title}</h2>
          <p className="muted">{feature.body}</p>
          <span className="feature-go">Open<i className="chevron" aria-hidden /></span>
        </Link>
      ))}
    </div>
  </main>
}
