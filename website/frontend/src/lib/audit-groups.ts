import type { AuditEntry } from '../types/api'

export type AuditGroup = { key: string; date: string; entries: AuditEntry[] }

// Stored timestamps are UTC ISO. Everything below renders in the *viewer's* zone
// rather than pretending to know theirs, which is also why the day boundary is
// computed from local dates: an entry at 23:30 UTC can legitimately be "today" for
// one reader and "yesterday" for another.
function localKey(date: Date) {
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`
}

function heading(date: Date, now: Date) {
  const today = localKey(now)
  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)

  const key = localKey(date)
  if (key === today) return 'Today'
  if (key === localKey(yesterday)) return 'Yesterday'
  // Within the last week the weekday is the useful anchor; beyond that it stops
  // being one, so the year-less full date takes over.
  const daysApart = Math.round((now.getTime() - date.getTime()) / 86_400_000)
  if (daysApart < 7) return date.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: date.getFullYear() === now.getFullYear() ? undefined : 'numeric' })
}

/** Group audit entries into consecutive runs by local calendar day. */
export function groupByDay(entries: AuditEntry[], now = new Date()): AuditGroup[] {
  const groups: AuditGroup[] = []
  for (const entry of entries) {
    const date = entry.created_at ? new Date(entry.created_at) : null
    // A row with a missing or unparseable timestamp still has to appear -- it is
    // a record of something that happened. It gets its own bucket rather than
    // being silently folded into whichever day happens to precede it.
    const valid = date && !Number.isNaN(date.getTime())
    const key = valid ? localKey(date) : 'unknown'
    const label = valid ? heading(date, now) : 'Undated'
    const last = groups[groups.length - 1]
    if (last && last.key === key) last.entries.push(entry)
    else groups.push({ key, date: label, entries: [entry] })
  }
  return groups
}

/** "14:02" in the viewer's locale and zone. */
export function timeOfDay(iso: string | null) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}
