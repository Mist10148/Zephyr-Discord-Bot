import { useEffect } from 'react'
import { Navigate, useSearchParams } from 'react-router-dom'
import { loginUrl, safeNext, useMe } from '../lib/auth'
import { haptic } from '../lib/haptics'
import { BackLink, GlassSurface, LargeTitleHeader, Skeleton } from '../components/ios'

// Only failures arrive as a query parameter; success is silent, and the SPA learns
// it by calling /me. Keep this in step with website/api/auth.py.
const MESSAGES: Record<string, string> = {
  not_configured: 'Sign-in is not configured on this server.',
  access_denied: 'You cancelled the Discord sign-in.',
  oauth_error: 'Discord refused the sign-in request.',
  invalid_request: 'That sign-in link was incomplete. Please try again.',
  state_mismatch: 'That sign-in attempt could not be verified. Please try again.',
  state_expired: 'That sign-in link expired. Please try again.',
  token_exchange_failed: 'Discord would not complete the sign-in. Please try again.',
  insufficient_scope: 'Zephyr needs both the identify and servers permissions to continue.',
  discord_unavailable: 'Discord is not responding. Please try again shortly.',
  discord_rate_limited: 'Discord is rate limiting us. Please try again in a minute.',
  session_unavailable: 'The session store is unavailable. Please try again shortly.',
}

export function Login() {
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const error = params.get('error')
  const me = useMe()

  // Existing installs still carry a service worker that swallows /api navigations,
  // which would eat the sign-in redirect. Kicking an update check on mount closes
  // that window well before any click, which is also why the affordance below stays
  // a plain <a> with no await in a click handler.
  useEffect(() => { navigator.serviceWorker?.getRegistration().then(registration => registration?.update()).catch(() => {}) }, [])

  // Render the skeleton first so an already-signed-in visitor does not see the
  // button flash before being redirected.
  if (me.isPending) return <main className="app narrow"><Skeleton lines={4} /></main>
  if (me.data) return <Navigate to={next} replace />

  return <main className="app narrow">
    <LargeTitleHeader title="Sign in" subtitle="Manage the Discord servers you already administer." />
    {error && <div className="error-banner" role="alert"><i aria-hidden>!</i><span>{MESSAGES[error] ?? 'Sign-in failed. Please try again.'}</span></div>}
    <GlassSurface>
      <p>Sign in with Discord to manage the servers you already administer.</p>
      <p className="muted">Zephyr reads your username and your server list. Nothing else, and it never posts as you.</p>
      {/* A real link, not a button: full-page navigation to Flask, which keeps
          keyboard, middle-click and copy-link behaviour that <Link> cannot give. */}
      <a className="ios-button block" href={loginUrl(next)} onClick={() => haptic()}>Continue with Discord</a>
    </GlassSurface>
    <BackLink to="/">Back</BackLink>
  </main>
}
