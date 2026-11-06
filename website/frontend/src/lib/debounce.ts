import { useEffect, useState } from 'react'

// The value as it was once it stopped changing for `delay` ms. Key a query on
// this rather than on the raw input, or react-query still sees a new key, and
// fires a request, per keystroke.
export function useDebouncedValue<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return debounced
}
