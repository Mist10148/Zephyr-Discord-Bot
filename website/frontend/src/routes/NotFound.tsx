import { useNavigate } from 'react-router-dom'
import { LargeTitleHeader, PressableButton } from '../components/ios'

// Replaces the old `*` route, which silently rendered Home. With /g/:guildId in the
// table, `*` now catches things like /g/1/music, and showing the marketing page
// there reads as a bug rather than a 404.
export function NotFound() {
  const navigate = useNavigate()
  return <main className="app narrow centred">
    <LargeTitleHeader title="Not found" subtitle="That page does not exist." />
    <PressableButton variant="secondary" onClick={() => navigate('/')}>Home</PressableButton>
  </main>
}
