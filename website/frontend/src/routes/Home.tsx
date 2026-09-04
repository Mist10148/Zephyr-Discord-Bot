import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { PressableButton } from '../components/ios'
import { FeatureDashboardIcon, FeatureSystemIcon, FeatureWeatherIcon } from '../components/icons'

const FEATURES = [
  { to: '/weather', title: 'Weather', body: 'Live conditions, a daily forecast, and heat-index advisories for any city.', Icon: FeatureWeatherIcon },
  { to: '/g', title: 'Dashboard', body: 'Sign in with Discord to manage music, alerts, AI and settings per server.', Icon: FeatureDashboardIcon },
  { to: '/commands', title: 'Commands', body: 'Every slash and prefix command Zephyr answers, searchable by name or alias.', Icon: FeatureSystemIcon },
]

export function Home() {
  const status = useQuery({ queryKey: ['status'], queryFn: () => api<{ bot: { online: boolean } }>('/status') })
  const navigate = useNavigate()
  const online = status.data?.bot.online
  // Three states, not two: "we have not asked yet" must not render as "offline".
  const statusClass = status.isPending ? '' : online ? 'ok' : 'off'
  const statusText = status.isPending ? 'Connecting…' : online ? 'Bot online' : 'Bot offline'

  return <main className="app">
    <section className="hero">
      <span className="hero-mark" aria-hidden>❍</span>
      <h1>Zephyr</h1>
      <p className="hero-tagline">A weather-first Discord companion — forecasts, music and AI, with a dashboard to match.</p>
      <span className={`status-pill ${statusClass}`.trim()} role="status" data-glass="1"><i aria-hidden />{statusText}</span>
      <div className="hero-actions">
        <PressableButton onClick={() => navigate('/weather')}>Check the weather</PressableButton>
        <PressableButton variant="secondary" onClick={() => navigate('/g')}>Open dashboard</PressableButton>
      </div>
    </section>

    <section className="feature-grid">
      {FEATURES.map(({ to, title, body, Icon }) => (
        <Link key={to} to={to} className="glass glass-regular glass-interactive feature-card" data-glass="1">
          <Icon />
          <h2>{title}</h2>
          <p>{body}</p>
          <span className="feature-go">Open<span className="chevron" aria-hidden>›</span></span>
        </Link>
      ))}
    </section>
  </main>
}
