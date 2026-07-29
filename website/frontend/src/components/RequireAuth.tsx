import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { isUnauthorized, isUnconfigured, useMe } from '../lib/auth'
import { LargeTitleHeader, Skeleton } from './ios'
import { ErrorNote } from './ErrorNote'

// Three distinct states, deliberately not collapsed into two:
//   pending  -> skeleton, so there is no blank flash and no premature redirect
//   401      -> redirect to /login, so the URL reflects the state and Back works
//   anything else (offline, 5xx) -> retry surface, NEVER a redirect
// That last one matters most: bouncing someone to the sign-in page because their
// connection blipped reads as a spurious logout, which is the worst auth UX bug.
export function RequireAuth({ children }: { children: ReactNode }) {
  const me = useMe()
  const location = useLocation()
  if (me.isPending) return <main className="app"><Skeleton lines={5} /></main>
  // A server with no OAuth application configured is a deployment state, not an
  // outage: send it to the sign-in screen, which explains it, rather than to a
  // "Try again" button that can never succeed.
  if (isUnconfigured(me.error)) return <Navigate to="/login?error=not_configured" replace />
  // replace: otherwise Back bounces between the guarded page and /login.
  if (isUnauthorized(me.error)) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />
  if (me.error) return <main className="app"><LargeTitleHeader title="Offline" /><ErrorNote error={me.error} onRetry={() => me.refetch()} /></main>
  return <>{children}</>
}
