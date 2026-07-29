// initials() stays module-local rather than exported: a camelCase *export* beside a
// component in a .tsx file is exactly what react-refresh/only-export-components
// flags, and CI fails on warnings.
function initials(name: string) { return name.split(/\s+/).filter(Boolean).slice(0, 2).map(word => word[0]).join('').toUpperCase() || '?' }

// Icon URLs come from the API (icon_url) so cdn.discordapp.com paths and the
// default-avatar index live in one place server-side. referrerPolicy stops the
// dashboard URL, which contains a guild id, leaking to Discord's CDN.
// alt="" plus aria-hidden on the monogram: the name is already the adjacent row
// label, so the image is decorative and must not be announced twice.
export function GuildIcon({ name, iconUrl, large = false }: { name: string; iconUrl: string | null; large?: boolean }) {
  const className = large ? 'guild-icon lg' : 'guild-icon'
  // Discord has no default *guild* icon -- embed/avatars is users only -- so an
  // iconless server gets an initials monogram.
  if (!iconUrl) return <span className={className} aria-hidden>{initials(name)}</span>
  return <span className={className}><img src={iconUrl} alt="" loading="lazy" referrerPolicy="no-referrer" /></span>
}

export function UserAvatar({ name, avatarUrl }: { name: string; avatarUrl: string | null }) {
  if (!avatarUrl) return <span className="guild-icon" aria-hidden>{initials(name)}</span>
  return <span className="guild-icon"><img src={avatarUrl} alt="" loading="lazy" referrerPolicy="no-referrer" /></span>
}
