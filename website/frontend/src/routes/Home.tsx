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
  const status = useQuery({
    queryKey: ['status'],
    queryFn: () => api<{ bot: { online: boolean; published_at?: number | null }; invite_url?: string | null }>('/status'),
    // Render's free web tier spins down, so a cold visitor's first request can
    // take many seconds. Polling means the pill corrects itself once the bot
    // reports in, rather than reading "offline" until a manual refresh.
    refetchInterval: query => (query.state.data?.bot.online ? false : 15_000),
  })
  const navigate = useNavigate()
  const online = status.data?.bot.online
  const inviteUrl = status.data?.invite_url
  // Four states, not three. "Waking" is the one 12.7 asks for: Render's free
  // tier spins the *web* service down, so on a cold visit the API answers late
  // and the bot may not have published a heartbeat yet -- which is not the same
  // thing as the bot being offline, and saying "Bot offline" to somebody
  // deciding whether to install it is both wrong and the worst possible moment.
  // A presence key that exists but is stale is exactly that state.
  const waking = !online && !!status.data?.bot.published_at
  const statusClass = status.isPending ? '' : online ? 'ok' : waking ? 'unknown' : 'off'
  const statusText = status.isPending
    ? 'Connecting…'
    : online ? 'Bot online' : waking ? 'Waking up…' : 'Bot offline'

  return <main className="app">
    <section className="hero">
      <span className="hero-mark" aria-hidden>❍</span>
      <h1>Zephyr</h1>
      <p className="hero-tagline">A weather-first Discord companion — forecasts, music and AI, with a dashboard to match.</p>
      <span className={`status-pill ${statusClass}`.trim()} role="status" data-glass="1"><i aria-hidden />{statusText}</span>
      {/* The invite is the primary action: this is a bot's website, and
          somebody landing here was previously offered "check the weather" and
          no way to install the thing at all. An anchor rather than
          PressableButton because it leaves the site -- the primitive
          deliberately has no href. */}
      <div className="hero-actions">
        {inviteUrl
          ? <a className="ios-button primary" href={inviteUrl} rel="noreferrer">Add Zephyr to Discord</a>
          : null}
        <PressableButton variant={inviteUrl ? 'secondary' : 'primary'} onClick={() => navigate('/weather')}>Check the weather</PressableButton>
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
