import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { PressableButton } from '../components/ios'

export function Home() {
  const status = useQuery({ queryKey: ['status'], queryFn: () => api<{ bot: { online: boolean } }>('/status') })
  const navigate = useNavigate()
  return <main className="app"><h1>Zephyr</h1><p>A weather-first Discord companion.</p><p>{status.isPending ? 'Connecting…' : status.data?.bot.online ? 'Bot online' : 'Bot offline'}</p><PressableButton onClick={() => navigate('/weather')}>Weather</PressableButton> <Link to="/g">Dashboard</Link> <Link to="/kitchen-sink">Design system</Link></main>
}
