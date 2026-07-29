import { Link } from 'react-router-dom'
import { LargeTitleHeader } from '../components/ios'

// Replaces the old `*` route, which silently rendered Home. With /g/:guildId in the
// table, `*` now catches things like /g/1/music, and showing the marketing page
// there reads as a bug rather than a 404.
export function NotFound() { return <main className="app"><LargeTitleHeader title="Not found" /><p>That page does not exist.</p><p><Link to="/">Home</Link></p></main> }
