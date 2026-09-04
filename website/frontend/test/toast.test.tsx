import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ToastHost } from '../src/components/ToastHost'
import { ApiError } from '../src/lib/api'
import { errorMessage, pushToast, resetToasts, useToastStore } from '../src/lib/toast'

afterEach(() => resetToasts())

describe('the toast host', () => {
  it('renders a live region even while empty', () => {
    render(<ToastHost />)
    // Mounted-and-empty on purpose: a live region inserted into the DOM at the
    // same moment as its content is not reliably announced.
    const region = document.querySelector('.toast-region')!
    expect(region).toHaveAttribute('aria-live', 'polite')
    expect(region.children).toHaveLength(0)
  })

  it('announces an error with role=alert and a neutral one with role=status', async () => {
    render(<ToastHost />)
    pushToast('error', 'Zephyr refused that request.')
    pushToast('neutral', 'Working on it.')

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Zephyr refused that request.'))
    expect(screen.getByRole('status')).toHaveTextContent('Working on it.')
  })

  it('stacks at most three, dropping the oldest', async () => {
    render(<ToastHost />)
    for (const n of [1, 2, 3, 4]) pushToast('error', `Message ${n}`)

    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(3))
    expect(screen.queryByText('Message 1')).not.toBeInTheDocument()
    expect(screen.getByText('Message 4')).toBeInTheDocument()
  })

  it('auto-dismisses success but keeps an error until dismissed', async () => {
    vi.useFakeTimers()
    try {
      render(<ToastHost />)
      pushToast('success', 'Queued Bohemian Rhapsody')
      pushToast('error', 'Nothing is playing.')
      expect(useToastStore.getState().toasts).toHaveLength(2)

      await vi.advanceTimersByTimeAsync(5000)
      // An error that vanishes before it is read is worse than a silent
      // failure, so only the success goes.
      const remaining = useToastStore.getState().toasts
      expect(remaining).toHaveLength(1)
      expect(remaining[0].tone).toBe('error')
    } finally {
      vi.useRealTimers()
    }
  })

  it('can be dismissed by hand', async () => {
    render(<ToastHost />)
    pushToast('error', 'Nothing is playing.')
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(0))
  })

  it('renders every toast inside the fixed region, never in page flow', async () => {
    render(<ToastHost />)
    pushToast('neutral', 'Anything')

    // jsdom does not load the stylesheet, so `position: fixed` itself is not
    // observable here. What this guards is the structural half: every toast is
    // a descendant of .toast-region, so none of them can be inlined at a call
    // site again -- which is the defect the host replaced.
    const toast = await waitFor(() => screen.getByRole('status'))
    expect(toast.closest('.toast-region')).not.toBeNull()
    expect(document.querySelectorAll('.toast')).toHaveLength(
      document.querySelectorAll('.toast-region .toast').length,
    )
  })
})

describe('errorMessage', () => {
  it('shows the Flask envelope message, which is written for users', () => {
    expect(errorMessage(new ApiError(409, 'refused', 'Nothing is playing.'))).toBe('Nothing is playing.')
  })

  it('never shows a fetch-level failure verbatim', () => {
    // "TypeError: Failed to fetch" is not something to put in front of anybody.
    expect(errorMessage(new TypeError('Failed to fetch'))).toBe('Network error — check your connection.')
  })
})
