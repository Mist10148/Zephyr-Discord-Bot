import { MutationCache, QueryClient } from '@tanstack/react-query'
import { ApiError } from './api'
import { errorMessage, pushToast } from './toast'

// Feedback is wired here rather than at each call site, and the split is
// deliberate.
//
// *Errors* go through the MutationCache, globally. Every mutation in the app
// then reports its failure whether or not anyone remembered -- including the
// screens nobody is currently editing, which is exactly where a silent failure
// would survive longest. A call site that wants to handle an error itself sets
// `meta.silent`.
//
// *Successes* stay per-mutation, via `meta.success`. Only the call site knows
// the right words: "Queued Bohemian Rhapsody" is useful, and a generic "Saved"
// fired after every toggle is noise that trains people to ignore the region.
//
// `meta.success` may be a string or a function of the variables, so a call site
// can name what it just did without threading state through the mutation.
declare module '@tanstack/react-query' {
  interface Register {
    mutationMeta: {
      success?: string | ((variables: unknown, data: unknown) => string | null)
      silent?: boolean
    }
  }
}

// Built per client rather than shared, so a test can construct an isolated
// client and still get the real feedback wiring. A shared MutationCache
// instance cannot be attached to two clients.
function feedbackMutationCache() {
  return new MutationCache({
    onError(error, _variables, _context, mutation) {
      if (mutation.meta?.silent) return
      pushToast('error', errorMessage(error))
    },
    onSuccess(data, variables, _context, mutation) {
      const success = mutation.meta?.success
      if (!success) return
      const message = typeof success === 'function' ? success(variables, data) : success
      if (message) pushToast('success', message)
    },
  })
}

// A 4xx is an answer, not a failure: retrying a 401 doubles every signed-out page
// load and delays the redirect to /login for no reason. 5xx and network-level
// TypeErrors keep the previous single retry.
export function createQueryClient(overrides: ConstructorParameters<typeof QueryClient>[0] = {}) {
  const { defaultOptions, ...rest } = overrides
  return new QueryClient({
    mutationCache: feedbackMutationCache(),
    ...rest,
    defaultOptions: {
      ...defaultOptions,
      queries: {
        retry: (count, error) => count < 1 && !(error instanceof ApiError && error.status < 500),
        staleTime: 30_000,
        ...defaultOptions?.queries,
      },
    },
  })
}

export const queryClient = createQueryClient()
