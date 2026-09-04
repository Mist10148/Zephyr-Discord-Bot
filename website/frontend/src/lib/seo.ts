// Per-route titles, descriptions and canonicals.
//
// Every route shared one static <title> ("Zephyr Weather"), so browser history
// and a row of tabs could not tell /commands from /weather. This fixes that
// half.
//
// It cannot fix social cards, and pretending otherwise would be worse than not
// trying: **no unfurler runs JavaScript**, so a `og:title` written here after
// mount is never seen by Discord, Slack or anything else. Those tags are static
// in index.html and describe the site. Per-route cards need server-side
// injection into the shell -- website/routes.py already knows every route's
// title and description, and spa.py already serves index.html with
// Cache-Control: no-cache, so it is a placeholder replace when it matters.
//
// A central table rather than a per-route component: a component is something
// to forget when adding a route, and a table is one place to look.

import { matchPath } from 'react-router-dom'

const SUFFIX = 'Zephyr'

type Meta = { title: string; description?: string; robots?: string }

/** Ordered: the first pattern that matches wins, so specific paths precede
 *  their prefixes. */
export const ROUTE_META: Array<[string, Meta]> = [
  ['/', { title: 'Zephyr — weather, music and AI for Discord', description: 'Forecasts and heat-index advisories, a full music player, and an AI companion — with a dashboard to configure it all.' }],
  ['/weather', { title: 'Weather', description: 'Live conditions, a daily forecast, air quality and heat-index advisories for any city.' }],
  ['/commands', { title: 'Commands', description: 'Every slash and prefix command Zephyr answers, searchable by name or alias.' }],
  ['/privacy', { title: 'Privacy', description: 'What Zephyr stores, why, and how to remove it.' }],
  ['/terms', { title: 'Terms', description: 'The terms for using Zephyr.' }],
  ['/settings', { title: 'Appearance', description: 'Theme, palette, density and units, kept in this browser.' }],
  // noindex on the internal review surface and on everything behind auth: a
  // crawler that reaches them finds an empty shell, and indexing that is worse
  // than not indexing it at all.
  ['/kitchen-sink', { title: 'Design system', robots: 'noindex' }],
  ['/login', { title: 'Sign in', robots: 'noindex' }],
  ['/g/:guildId/music', { title: 'Music', robots: 'noindex' }],
  ['/g/:guildId/weather-alerts', { title: 'Weather alerts', robots: 'noindex' }],
  ['/g/:guildId/ai', { title: 'AI', robots: 'noindex' }],
  ['/g/:guildId/settings', { title: 'Server settings', robots: 'noindex' }],
  ['/g/:guildId/audit', { title: 'Audit log', robots: 'noindex' }],
  ['/g/:guildId', { title: 'Server', robots: 'noindex' }],
  ['/g', { title: 'Your servers', robots: 'noindex' }],
]

const NOT_FOUND: Meta = { title: 'Not found', robots: 'noindex' }

export function metaFor(pathname: string): Meta {
  for (const [pattern, meta] of ROUTE_META) {
    // `end: true` so '/' does not swallow every path below it.
    if (matchPath({ path: pattern, end: true }, pathname)) return meta
  }
  return NOT_FOUND
}

/** The document title. The home page carries the full phrase already, so it is
 *  not suffixed into "Zephyr — … · Zephyr". */
export function titleFor(pathname: string): string {
  const { title } = metaFor(pathname)
  return title.includes(SUFFIX) ? title : `${title} · ${SUFFIX}`
}

/** Set or create one `<meta>`/`<link>` in the head.
 *
 *  Written by hand rather than with react-helmet-async: that would mean a
 *  provider, a dependency and a per-route component to forget, for four tag
 *  shapes and about forty lines.
 */
export function applyMeta(pathname: string, origin: string): void {
  const meta = metaFor(pathname)
  document.title = titleFor(pathname)

  setMeta('name', 'description', meta.description ?? '')
  setMeta('name', 'robots', meta.robots ?? 'index, follow')
  setLink('canonical', `${origin}${pathname === '/' ? '/' : pathname.replace(/\/$/, '')}`)
}

function setMeta(attribute: 'name' | 'property', key: string, content: string): void {
  if (!content) {
    document.head.querySelector(`meta[${attribute}="${key}"]`)?.remove()
    return
  }
  let element = document.head.querySelector<HTMLMetaElement>(`meta[${attribute}="${key}"]`)
  if (!element) {
    element = document.createElement('meta')
    element.setAttribute(attribute, key)
    document.head.appendChild(element)
  }
  element.setAttribute('content', content)
}

function setLink(rel: string, href: string): void {
  let element = document.head.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`)
  if (!element) {
    element = document.createElement('link')
    element.setAttribute('rel', rel)
    document.head.appendChild(element)
  }
  element.setAttribute('href', href)
}
