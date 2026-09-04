import type { Units } from './preferences'

// The API converts the values; this only labels them. Open-Meteo is asked for
// metric or imperial through the `units` query parameter (see
// website/api/weather.py), so a number arriving here is already in the
// requested system and must never be converted a second time.
//
// C8 added the metric/imperial preference and nothing on the page declared
// which one was in use, so "Wind 12" was genuinely ambiguous -- km/h or mph, mm
// or inches. The preference was read only to build the query key and the URL,
// never to render anything.
const LABELS = {
  metric: { speed: 'km/h', length: 'mm', temperature: '°C' },
  imperial: { speed: 'mph', length: 'in', temperature: '°F' },
} as const

export type Dimension = keyof (typeof LABELS)['metric']

export function unitLabel(units: Units, dimension: Dimension): string {
  return LABELS[units][dimension]
}

/** A dimensioned number with its unit, or an em dash when there is none.
 *
 * Returns a dash rather than an empty string for a missing value: a chip
 * reading "Wind" with nothing after it looks like a rendering bug, while "Wind
 * —" reads as "the station did not report this".
 */
export function formatUnit(value: number | null | undefined, units: Units, dimension: Dimension): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const label = LABELS[units][dimension]
  // Temperature keeps the degree tight against the number, the way every other
  // temperature on the page already renders; the rest take a space.
  return dimension === 'temperature' ? `${value}${label}` : `${value} ${label}`
}

/** Just the degree mark, for the many places already rendering a bare `°`.
 *  The scale is declared once per page rather than repeated on every number. */
export const degree = '°'
