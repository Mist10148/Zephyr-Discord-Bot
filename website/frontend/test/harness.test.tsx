import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../src/lib/api'
import { renderWithQuery, stubApi } from './helpers'

function Probe() {
  const q = useQuery({ queryKey: ['probe'], queryFn: () => api<{ name: string }>('/probe') })
  if (q.isPending) return <p>loading</p>
  if (q.isError) return <p>failed: {String((q.error as Error).message)}</p>
  return <p>got {q.data?.name}</p>
}

describe('harness', () => {
  it('answers a query through stubApi', async () => {
    const { calls } = stubApi({ '/probe': { body: { name: 'zephyr' } } })
    renderWithQuery(<Probe />)
    await waitFor(() => expect(screen.getByText('got zephyr')).toBeInTheDocument())
    expect(calls).toEqual(['/probe'])
  })

  it('surfaces an error envelope without retrying', async () => {
    stubApi({ '/probe': { status: 500, body: { error: { code: 'boom', message: 'Kaboom' } } } })
    renderWithQuery(<Probe />)
    await waitFor(() => expect(screen.getByText(/Kaboom/)).toBeInTheDocument())
  })

  it('has matchMedia, geolocation and clipboard', () => {
    expect(window.matchMedia('(prefers-color-scheme: dark)').matches).toBe(false)
    expect(navigator.geolocation.getCurrentPosition).toBeTypeOf('function')
    expect(navigator.clipboard.writeText).toBeTypeOf('function')
  })
})
