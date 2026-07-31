import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { SegmentedControl, Toggle } from '../src/components/ios'

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
