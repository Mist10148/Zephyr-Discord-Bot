import { useNavigate } from 'react-router-dom'
import { useLogout, useMe } from '../lib/auth'
import { haptic } from '../lib/haptics'
import { GuildIcon, UserAvatar } from '../components/DiscordAvatar'
import { ErrorNote } from '../components/ErrorNote'
import { BackLink, GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton } from '../components/ios'

// bot_present is a tri-state: null means the bot has never published a snapshot, so
// saying "not added" would be a guess. Servers without the bot are still listed --
// hiding a server somebody administers is an unexplainable dead end. The dot colour
// carries the same three states as the text, so the row is scannable without
// reading every line.
function presence(botPresent: boolean | null) {
  if (botPresent === null) return { text: 'Bot status unknown', tone: 'unknown' }
  return botPresent
    ? { text: 'Zephyr is in this server', tone: 'ok' }
    : { text: 'Zephyr is not in this server yet', tone: 'off' }
}

export function Guilds() {
  const me = useMe()
  const logout = useLogout()
  const navigate = useNavigate()
  // RequireAuth has already gated this route, so data is present. The non-null
  // assertion matches the house style (see Weather's place!.latitude).
  const { user, guilds, invite_url: inviteUrl, guilds_stale: stale } = me.data!

  return <main className="app medium">
    <LargeTitleHeader title="Your servers" />

    <ListGroup>
      <ListRow leading={<UserAvatar name={user.username} avatarUrl={user.avatar_url} />} label={user.global_name ?? user.username} detail={`@${user.username}`} className="strong-row" />
    </ListGroup>

    {stale && <GlassSurface tier="thin" className="notice">
      <i className="dot unknown" aria-hidden />
      <p>Your server list may be out of date. <a href="/api/v1/auth/login?next=%2Fg">Refresh it</a>.</p>
    </GlassSurface>}

    {guilds.length === 0
      ? <GlassSurface>
        <p>No servers yet.</p>
        <p className="muted">You can only manage servers where you have the Manage Server permission. Invite Zephyr to one, then reload this page.</p>
        <p><a className="ios-button" href={inviteUrl}>Add Zephyr to a server</a></p>
      </GlassSurface>
      : <ListGroup>
        {guilds.map(guild => {
          const { text, tone } = presence(guild.bot_present)
          return <ListRow
            key={guild.id}
            to={`/g/${guild.id}`}
            leading={<GuildIcon name={guild.name} iconUrl={guild.icon_url} />}
            label={guild.name}
            detail={<span className="row-detail"><i className={`dot ${tone}`} aria-hidden />{text}</span>}
            className="strong-row"
          />
        })}
      </ListGroup>}

    {logout.error && <ErrorNote error={logout.error} onRetry={() => logout.reset()} />}

    <div className="actions">
      <PressableButton variant="danger" disabled={logout.isPending} onClick={() => { haptic(15); logout.mutate(undefined, { onSuccess: () => navigate('/login', { replace: true }) }) }}>
        {logout.isPending ? 'Signing out…' : 'Sign out'}
      </PressableButton>
      <BackLink to="/">Back</BackLink>
    </div>
  </main>
}
