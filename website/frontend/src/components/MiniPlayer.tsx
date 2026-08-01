import { Link, useParams } from 'react-router-dom'
import { usePlayer } from '../lib/player'

export function MiniPlayer() {
  const { guildId } = useParams(); const player = usePlayer(guildId)
  if (!guildId || !player.data?.live || !player.data.track) return null
  return <Link className="mini-player" to={`/g/${guildId}/music`} data-glass="1"><span className="mini-player-dot" aria-hidden />Now playing: <b>{player.data.track.title}</b></Link>
}
