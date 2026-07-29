import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from './api'
import type { Me } from '../types/api'

// No context provider and no zustand. The session cookie is HttpOnly, so the
// frontend physically cannot read whether it is signed in -- "am I signed in?" is
// literally a server query, which is what TanStack Query already is here. A
// provider would re-wrap this same query; a store would need manual hydration and
// could drift from the server.
//
// This is a .ts file on purpose: eslint-plugin-react-refresh only scans .jsx/.tsx,
// so exporting hooks alongside plain functions here cannot trip
// react-refresh/only-export-components, which CI treats as an error via
// --max-warnings=0.
export function useMe() { return useQuery({ queryKey: ['me'], queryFn: () => api<Me>('/me') }) }
export function isUnauthorized(error: unknown) { return error instanceof ApiError && error.status === 401 }
// A server with no OAuth application configured answers 503 auth_not_configured.
// That is a deployment state, not an outage, so it belongs on the sign-in screen
// where it can be explained -- not behind a "Try again" button that will never work.
export function isUnconfigured(error: unknown) { return error instanceof ApiError && error.code === 'auth_not_configured' }
// Path-only, always leading-slash. The backend re-validates it anyway (rejecting
// //evil.com, backslash tricks and absolute URLs) and falls back to /g.
export function loginUrl(next: string) { return `/api/v1/auth/login?next=${encodeURIComponent(next)}` }
export function safeNext(value: string | null | undefined) { return value && value.startsWith('/') && !value.startsWith('//') && !value.includes('\\') ? value : '/g' }
// clear(), not invalidate(): after signing out, cached guild data must be evicted
// rather than refetched.
export function useLogout() { const client = useQueryClient(); return useMutation({ mutationFn: () => api<null>('/auth/logout', { method: 'POST' }), onSettled: () => client.clear() }) }
