import { Link, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

// There was no footer at all: no support link, no repository, no legal links,
// no copyright. That is where most of the "is this a real product" signal
// lives, and it is where 12.2's pages have to be linked from -- a privacy
// policy nobody can find is not a published policy.
//
// Hidden on /login, which is a single-purpose screen, and inside the dashboard,
// where a marketing footer under a music remote is noise.

type Status = { support_url?: string | null; repository_url?: string | null }

export function SiteFooter() {
  const { pathname } = useLocation()
  const links = useQuery({
    queryKey: ['site-links'],
    queryFn: () => api<Status>('/site'),
    staleTime: 60 * 60_000,
    retry: false,
  })

  if (pathname === '/login' || pathname.startsWith('/g')) return null

  const support = links.data?.support_url
  const repository = links.data?.repository_url

  return (
    <footer className="site-footer">
      <div className="site-footer-inner">
        <nav aria-label="Site">
          <Link to="/commands">Commands</Link>
          <Link to="/privacy">Privacy</Link>
          <Link to="/terms">Terms</Link>
          {/* The design-system page, demoted from the landing page's hero grid
              in 11.4. It stays reachable, as a footer link. */}
          <Link to="/kitchen-sink">Design system</Link>
          {/* Absent rather than dead: a deployment with no support server
              should show no support link at all. */}
          {support && <a href={support} rel="noreferrer">Support</a>}
          {repository && <a href={repository} rel="noreferrer">Source</a>}
        </nav>
        <p className="site-footer-note">
          Zephyr is a Discord bot. Not affiliated with Discord Inc.
        </p>
      </div>
    </footer>
  )
}
