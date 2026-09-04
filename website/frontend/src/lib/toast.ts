import { create } from 'zustand'
import { ApiError } from './api'

export type ToastTone = 'neutral' | 'error' | 'success'
/** `action` is what makes undo-instead-of-confirm reachable. It was
 *  implemented before this and rendered after the queue list, so with a long
 *  queue it spent its whole five-second life below the fold. */
export type ToastAction = { label: string; onClick(): void }
export type Toast = { id: number; tone: ToastTone; message: string; action?: ToastAction }

/** At most three. A fourth would cover the tab bar on a phone, and a stack
 *  nobody can read is the same as no feedback. The oldest is dropped. */
const MAX_VISIBLE = 3
/** Neutral and success dismiss themselves; errors stay until dismissed, because
 *  an error that vanishes before it is read is worse than a silent failure. */
const AUTO_DISMISS_MS = 4000

type ToastStore = {
  toasts: Toast[]
  push(tone: ToastTone, message: string, action?: ToastAction): number
  dismiss(id: number): void
}

let nextId = 1
const timers = new Map<number, number>()

// zustand, not context, and this is the case lib/auth.ts's no-store note
// excludes. That note is about *session* state -- "am I signed in?" is a server
// query, a store would need hydration and could drift from the server. Neither
// applies to ephemeral client state with no server counterpart.
//
// The decisive reason is structural rather than stylistic: lib/query.ts is not a
// component. Routing mutation failures through the MutationCache means the
// emitter has to be callable from module scope, and a React context provider
// physically cannot be. A store can.
export const useToastStore = create<ToastStore>(set => ({
  toasts: [],
  push(tone, message, action) {
    const id = nextId++
    set(state => ({ toasts: [...state.toasts, { id, tone, message, action }].slice(-MAX_VISIBLE) }))
    if (tone !== 'error') {
      timers.set(id, window.setTimeout(() => useToastStore.getState().dismiss(id), AUTO_DISMISS_MS))
    }
    return id
  },
  dismiss(id) {
    const timer = timers.get(id)
    if (timer !== undefined) { window.clearTimeout(timer); timers.delete(id) }
    set(state => ({ toasts: state.toasts.filter(toast => toast.id !== id) }))
  },
}))

/** Emit a toast from anywhere, including module scope. */
export const pushToast = (tone: ToastTone, message: string, action?: ToastAction) => useToastStore.getState().push(tone, message, action)
export const dismissToast = (id: number) => useToastStore.getState().dismiss(id)

/** The subscription a component uses. Named for the call site, not the store. */
export function useToast() {
  const push = useToastStore(state => state.push)
  return {
    success: (message: string, action?: ToastAction) => push('success', message, action),
    error: (message: string, action?: ToastAction) => push('error', message, action),
    info: (message: string, action?: ToastAction) => push('neutral', message, action),
  }
}

export const useToasts = () => useToastStore(state => state.toasts)

/** The user-facing text for a thrown value.
 *
 * ApiError messages come from the Flask envelope and are written for users.
 * Anything else is a fetch-level failure, and "TypeError: Failed to fetch" is
 * not something to show anybody. Same rule ErrorNote has always applied --
 * lifted here so the toast host and ErrorNote cannot drift apart.
 */
export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Network error — check your connection.'
}

/** Reset between tests. The store is module state, so it outlives a render. */
export function resetToasts() {
  for (const timer of timers.values()) window.clearTimeout(timer)
  timers.clear()
  useToastStore.setState({ toasts: [] })
}
