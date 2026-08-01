import { describe, expect, it } from 'vitest'
import { groupByDay, timeOfDay } from '../src/lib/audit-groups'
import { airQualityLabel, heatAdvisory, weatherGlyph } from '../src/lib/weather-icons'
import type { AuditEntry } from '../src/types/api'

function entry(id: number, createdAt: string | null): AuditEntry {
  return { id, guild_id: '1', actor_id: '2', action: 'settings.update', payload: null, source: 'web', created_at: createdAt }
}

describe('weatherGlyph', () => {
  it('maps clear and mainly-clear to the sun', () => {
    expect(weatherGlyph(0)).toBe('sun')
    expect(weatherGlyph(1)).toBe('sun')
  })

  it('maps cloud cover and fog to the cloud', () => {
    for (const code of [2, 3, 45, 48]) expect(weatherGlyph(code)).toBe('cloud')
  })

  it('maps every kind of precipitation to the rain glyph', () => {
    // Drizzle, freezing rain, snow, showers and thunderstorms all mean "something
    // is falling", which is the true half of the forecast given a three-glyph set.
    for (const code of [51, 61, 66, 71, 77, 80, 86, 95, 99]) expect(weatherGlyph(code)).toBe('rain')
  })

  it('falls back to cloud when the code is missing', () => {
    expect(weatherGlyph(null)).toBe('cloud')
    expect(weatherGlyph(undefined)).toBe('cloud')
  })
})

describe('airQualityLabel', () => {
  it('title-cases the band and drops the underscores', () => {
    expect(airQualityLabel('very_poor')).toBe('Very poor')
    expect(airQualityLabel('good')).toBe('Good')
  })

  it('returns null when there is no band', () => {
    expect(airQualityLabel(null)).toBeNull()
    expect(airQualityLabel(undefined)).toBeNull()
  })
})

describe('heatAdvisory', () => {
  it('is true only for the levels that warrant a chip', () => {
    expect(heatAdvisory('possible')).toBe(true)
    expect(heatAdvisory('likely')).toBe(true)
    expect(heatAdvisory('certain')).toBe(true)
  })

  it('treats "unknown" as no advisory rather than as one', () => {
    // "We could not work it out" is not the same answer as "there is one", and
    // showing a danger chip for it would be a false alarm.
    expect(heatAdvisory('unknown')).toBe(false)
    expect(heatAdvisory('none')).toBe(false)
    expect(heatAdvisory(null)).toBe(false)
  })
})

describe('groupByDay', () => {
  const now = new Date(2026, 7, 1, 12, 0, 0) // 1 Aug 2026, local

  it('labels the current and previous local days', () => {
    const groups = groupByDay([
      entry(1, new Date(2026, 7, 1, 9, 0).toISOString()),
      entry(2, new Date(2026, 6, 31, 22, 0).toISOString()),
    ], now)
    expect(groups.map(group => group.date)).toEqual(['Today', 'Yesterday'])
  })

  it('keeps entries from the same day in one group', () => {
    const groups = groupByDay([
      entry(1, new Date(2026, 7, 1, 14, 0).toISOString()),
      entry(2, new Date(2026, 7, 1, 9, 0).toISOString()),
      entry(3, new Date(2026, 7, 1, 1, 0).toISOString()),
    ], now)
    expect(groups).toHaveLength(1)
    expect(groups[0].entries.map(item => item.id)).toEqual([1, 2, 3])
  })

  it('gives undated entries their own bucket', () => {
    // A row with no usable timestamp is still a record of something that happened;
    // folding it into whichever day precedes it would date it wrongly.
    const groups = groupByDay([
      entry(1, new Date(2026, 7, 1, 9, 0).toISOString()),
      entry(2, null),
    ], now)
    expect(groups.map(group => group.date)).toEqual(['Today', 'Undated'])
  })

  it('returns nothing for an empty log', () => {
    expect(groupByDay([], now)).toEqual([])
  })
})

describe('timeOfDay', () => {
  it('is empty rather than "Invalid Date" for unusable input', () => {
    expect(timeOfDay(null)).toBe('')
    expect(timeOfDay('not a date')).toBe('')
  })

  it('formats a real timestamp', () => {
    expect(timeOfDay(new Date(2026, 7, 1, 14, 2).toISOString())).toMatch(/\d/)
  })
})
