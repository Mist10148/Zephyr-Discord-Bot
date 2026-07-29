// `credentials` is deliberately not set: the fetch default is already 'same-origin',
// which covers both production and the Vite dev proxy (same origin from the
// browser's point of view). 'include' would be actively wrong -- it would demand
// CORS headers we do not send.
export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string, readonly detail: unknown = null) { super(message); this.name = 'ApiError' }
}
type Options = { method?: string; body?: unknown; headers?: Record<string, string>; signal?: AbortSignal }
type ErrorBody = { error?: { code?: string; message?: string; detail?: unknown } }
// The session cookie is HttpOnly and unreadable; this one exists only to carry the
// CSRF token back. The server compares the header against the session's own stored
// value, never against this cookie, so it needs no signing.
function csrfToken() { return document.cookie.match(/(?:^|; )zephyr_csrf=([^;]*)/)?.[1] ?? '' }
export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = { ...options.headers }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && method !== 'HEAD') headers['X-Zephyr-CSRF'] = csrfToken()
  const response = await fetch(`/api/v1${path}`, { method, headers, signal: options.signal, body: options.body === undefined ? undefined : JSON.stringify(options.body) })
  const payload = response.status === 204 ? null : await response.json().catch(() => null)
  if (!response.ok) { const body = (payload as ErrorBody | null)?.error; throw new ApiError(response.status, body?.code ?? 'http_error', body?.message ?? `Request failed (${response.status})`, body?.detail) }
  return payload as T
}
