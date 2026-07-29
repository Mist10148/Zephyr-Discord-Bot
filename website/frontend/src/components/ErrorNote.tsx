import { useEffect } from 'react'
import { ApiError } from '../lib/api'
import { haptic } from '../lib/haptics'
import { CapsuleToast, PressableButton } from './ios'

// The single error surface for the dashboard. Firing the haptic from an effect here
// means every error path buzzes without any call site having to remember.
// No error boundary: it could not catch async query errors anyway.
export function ErrorNote({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  useEffect(() => { haptic([10, 40, 10]) }, [error])
  // ApiError messages come from the Flask envelope and are meant for users.
  // Anything else is a fetch-level failure, and "TypeError: Failed to fetch" is not.
  return <div className="stack"><CapsuleToast tone="error">{error instanceof ApiError ? error.message : 'Network error — check your connection.'}</CapsuleToast>{onRetry && <PressableButton variant="secondary" onClick={onRetry}>Try again</PressableButton>}</div>
}
