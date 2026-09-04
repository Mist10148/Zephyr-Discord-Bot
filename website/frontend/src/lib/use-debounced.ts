import { useEffect, useState } from 'react'

/** The value, held back until it has stopped changing for `delay` ms.
 *
 * Lives in a `.ts` file with no component exports, per the react-refresh rule
 * that `eslint src --max-warnings=0` makes fatal.
 *
 * Debouncing the *value* rather than the request is deliberate: a react-query
 * key built from the raw input is a new key per character, so throttling the
 * fetch alone would still fill the cache with one entry per keystroke and still
 * flip the query back to pending on every letter. Keying on the debounced value
 * means one key, one request, one loading state per pause.
 */
export function useDebounced<T>(value: T, delay = 250): T {
  const [settled, setSettled] = useState(value)

  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])

  return settled
}
