// WMO weather codes -> the three glyphs the design draws.
//
// The API sends `description`, `icon` and `weather_code` for both `current` and
// each `daily` row. The code is the one that is stable: `description` is prose
// meant for a human and `icon` is an emoji-ish string, so matching on either would
// break the moment the wording is tuned server-side.
//
// Snow maps to `rain` rather than `cloud`. The icon set has no snow glyph, and of
// the two, "something is falling" is the true half of the forecast -- a cloud would
// say the opposite. Zephyr's audience is tropical, so this is a rare edge either way.
export type WeatherGlyph = 'sun' | 'cloud' | 'rain'

export function weatherGlyph(code: number | null | undefined): WeatherGlyph {
  if (code == null) return 'cloud'
  if (code === 0 || code === 1) return 'sun'          // clear / mainly clear
  if (code === 2 || code === 3) return 'cloud'        // partly cloudy / overcast
  if (code === 45 || code === 48) return 'cloud'      // fog
  if (code >= 51 && code <= 67) return 'rain'         // drizzle + freezing drizzle + rain
  if (code >= 71 && code <= 77) return 'rain'         // snow, snow grains
  if (code >= 80 && code <= 86) return 'rain'         // showers
  if (code >= 95) return 'rain'                       // thunderstorms
  return 'cloud'
}

// Bands come from the European AQI. Title-case them for display rather than
// leaking `very_poor` into the UI.
export function airQualityLabel(band: string | null | undefined): string | null {
  if (!band) return null
  return band.replace(/_/g, ' ').replace(/^./, character => character.toUpperCase())
}

// `class_suspension.level` is an advisory the API already computes from the heat
// index. `unknown` means it could not be worked out, which is not the same as "no
// advisory" -- neither is worth a chip, but they are worth not conflating.
export function heatAdvisory(level: string | null | undefined): boolean {
  return level === 'possible' || level === 'likely' || level === 'certain'
}
