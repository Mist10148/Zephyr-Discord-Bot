import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { SegmentedControl, Skeleton, Toggle } from '../src/components/ios'

// A first spec that proves the runner works end to end AND locks the Phase 7 a11y
// contract on the primitives: these controls are what a keyboard/screen-reader user
// actually operates, so their aria state is behaviour, not decoration.

function ToggleHarness() {
  const [on, setOn] = useState(false)
  return <Toggle checked={on} onChange={setOn} />
}

function SegmentedHarness() {
  const [value, setValue] = useState('off')
  return <SegmentedControl values={['off', 'track', 'queue']} value={value} onChange={setValue} />
}

describe('Toggle', () => {
  it('exposes its state through aria-pressed and flips on click', () => {
    render(<ToggleHarness />)
    const button = screen.getByRole('button')
    expect(button).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('SegmentedControl', () => {
  it('marks exactly the selected segment as pressed', () => {
    render(<SegmentedHarness />)
    expect(screen.getByRole('button', { name: 'off' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'queue' }))
    expect(screen.getByRole('button', { name: 'queue' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'off' })).toHaveAttribute('aria-pressed', 'false')
  })
})

describe('Skeleton', () => {
  it('defaults to prose bars', () => {
    render(<Skeleton lines={4} />)
    expect(document.querySelectorAll('.skeleton > i')).toHaveLength(4)
  })

  it('shapes itself like a list group', () => {
    render(<Skeleton variant="rows" count={3} />)
    // A bordered card with hairline-separated rows, each carrying a label and a
    // value bar -- so the swap to real rows does not move anything.
    expect(document.querySelectorAll('.skeleton-row')).toHaveLength(3)
    expect(document.querySelectorAll('.skeleton-row .sk-label')).toHaveLength(3)
    expect(document.querySelectorAll('.skeleton-row .sk-value')).toHaveLength(3)
  })

  it('shapes itself like a widget grid and a now-playing block', () => {
    const { unmount } = render(<Skeleton variant="cards" count={2} />)
    expect(document.querySelectorAll('.skeleton-card')).toHaveLength(2)
    unmount()

    render(<Skeleton variant="now-playing" />)
    expect(document.querySelector('.skeleton-np .sk-art')).not.toBeNull()
    expect(document.querySelector('.skeleton-np .sk-title')).not.toBeNull()
  })

  it('stays busy for assistive tech in every variant', () => {
    for (const variant of ['lines', 'rows', 'cards', 'now-playing'] as const) {
      const { unmount } = render(<Skeleton variant={variant} />)
      expect(document.querySelector('[aria-busy="true"]')).not.toBeNull()
      unmount()
    }
  })
})
