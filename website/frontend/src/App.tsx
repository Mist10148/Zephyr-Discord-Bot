import { useQuery } from '@tanstack/react-query'
import { Link, Route, Routes } from 'react-router-dom'
import { api } from './lib/api'

function Home() {
  const status = useQuery({ queryKey: ['status'], queryFn: () => api<{ bot: { online: boolean } }>('/status') })
  return <main className="app"><h1>Zephyr</h1><p>A weather-first Discord companion.</p><p>{status.isPending ? 'Connecting…' : status.data?.bot.online ? 'Bot online' : 'Bot offline'}</p><Link to="/kitchen-sink">Design system</Link></main>
}
function KitchenSink() { return <main className="app"><h1>Kitchen Sink</h1><p>iOS primitives arrive in the next commit.</p><Link to="/">Back</Link></main> }
export default function App() { return <Routes><Route path="/" element={<Home />} /><Route path="/kitchen-sink" element={<KitchenSink />} /><Route path="*" element={<Home />} /></Routes> }
