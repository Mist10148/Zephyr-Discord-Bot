import { QueryClient } from '@tanstack/react-query'
import { ApiError } from './api'
// A 4xx is an answer, not a failure: retrying a 401 doubles every signed-out page
// load and delays the redirect to /login for no reason. 5xx and network-level
// TypeErrors keep the previous single retry.
export const queryClient = new QueryClient({ defaultOptions: { queries: { retry: (count, error) => count < 1 && !(error instanceof ApiError && error.status < 500), staleTime: 30_000 } } })
