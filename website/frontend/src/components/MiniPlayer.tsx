import { Link, useMatch } from 'react-router-dom'
import { usePlayer } from '../lib/player'

// Mounted outside <Routes> (see App.tsx), where useParams() is always empty:
// that is why this never rendered. Match the guild route explicitly instead.
export function MiniPlayer() {
  const guildMatch = useMatch('/g/:guildId/*'); const onMusicPage = useMatch('/g/:guildId/music')
  const guildId = onMusicPage ? undefined : guildMatch?.params.guildId; const player = usePlayer(guildId)
  if (!guildId || !player.data?.live || !player.data.track) return null
  return <Link className="mini-player" to={`/g/${guildId}/music`} data-glass="1"><span className="mini-player-dot" aria-hidden />Now playing: <b>{player.data.track.title}</b></Link>
}
