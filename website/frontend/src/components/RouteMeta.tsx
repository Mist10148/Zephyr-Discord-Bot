import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { applyMeta } from '../lib/seo'

// Mounted once from AppShell, so a new route cannot forget to include it --
// which is the failure mode a per-route <Helmet> has.
export function RouteMeta() {
  const { pathname } = useLocation()
  useEffect(() => {
    applyMeta(pathname, window.location.origin)
  }, [pathname])
  return null
}
