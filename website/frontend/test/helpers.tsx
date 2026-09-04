// Shared harness for route-level specs. Not collected: vitest.config.ts's
// `include` is 'test/**/*.test.{ts,tsx}', so this file is only ever imported.
//
// It exists because every route in this app needs three things a bare `render`
// does not give it -- a QueryClientProvider, a Router, and a `fetch` that
// answers -- and writing those three by hand per spec is how route specs end up
// not being written at all.

import type { ReactElement, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { render } from '@testing-library/react'
import { vi } from 'vitest'

export function testQueryClient() {
  return new QueryClient({
    defaultOptions: {
      // retry:false so a spec asserting an error state sees it on the first
      // rejection instead of waiting out lib/query.ts's one retry, and gcTime:0
      // so nothing survives into the next spec through the cache.
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  })
}

export function renderWithQuery(
  ui: ReactElement,
  { route = '/', client = testQueryClient() }: { route?: string; client?: QueryClient } = {},
) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
    </QueryClientProvider>
  )
  return { client, ...render(ui, { wrapper: Wrapper }) }
}

type StubRoute =
  | { status?: number; body?: unknown }
  | ((url: URL, init?: RequestInit) => { status?: number; body?: unknown })

/** Install a `fetch` that answers by pathname.
 *
 * Keys are matched as a prefix of `pathname + search`, after lib/api.ts's
 * '/api/v1' prefix, so '/weather' matches '/api/v1/weather?lat=…'. A key with a
 * query string matches only that query, which is how a spec asserts on the
 * *debounced* value of a search rather than on every keystroke.
 *
 * Anything unmatched rejects loudly rather than hanging: an unhandled request
 * in a route spec is a bug in the spec, and a pending query looks like a
 * loading-state assertion passing for the wrong reason.
 */
export function stubApi(routes: Record<string, StubRoute>) {
  const calls: string[] = []
  const handler = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const url = new URL(raw, 'http://localhost')
    const path = `${url.pathname.replace(/^\/api\/v1/, '')}${url.search}`
    calls.push(path)

    const key = Object.keys(routes)
      .sort((a, b) => b.length - a.length) // most specific first
      .find(candidate => path.startsWith(candidate))
    if (key === undefined) throw new Error(`stubApi: no route for ${path}`)

    const route = routes[key]
    const { status = 200, body = null } = typeof route === 'function' ? route(url, init) : route
    return new Response(body === null ? null : JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })
  })

  Object.defineProperty(globalThis, 'fetch', { value: handler, writable: true, configurable: true })
  return { calls, handler }
}
