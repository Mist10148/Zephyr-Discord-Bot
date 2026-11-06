import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useDebouncedValue } from '../src/lib/debounce'

describe('useDebouncedValue', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('starts at the initial value without waiting', () => {
    const { result } = renderHook(() => useDebouncedValue('Iloilo', 250))
    expect(result.current).toBe('Iloilo')
  })

  it('only settles once typing pauses', () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 250), { initialProps: { value: 'M' } })
    rerender({ value: 'Ma' })
    act(() => { vi.advanceTimersByTime(100) })
    rerender({ value: 'Man' })
    act(() => { vi.advanceTimersByTime(200) })
    expect(result.current).toBe('M')
    act(() => { vi.advanceTimersByTime(50) })
    expect(result.current).toBe('Man')
  })
})
