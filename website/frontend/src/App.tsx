import { useQuery } from '@tanstack/react-query'
import { Link, Route, Routes } from 'react-router-dom'
import { api } from './lib/api'
import { GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, SegmentedControl, Toggle, WidgetGrid } from './components/ios'
import { useState } from 'react'

function Home() {
  const status = useQuery({ queryKey: ['status'], queryFn: () => api<{ bot: { online: boolean } }>('/status') })
  return <main className="app"><h1>Zephyr</h1><p>A weather-first Discord companion.</p><p>{status.isPending ? 'Connecting…' : status.data?.bot.online ? 'Bot online' : 'Bot offline'}</p><Link to="/kitchen-sink">Design system</Link></main>
}
function KitchenSink() { const [selected, setSelected] = useState('Today'); const [enabled, setEnabled] = useState(true); return <main className="app"><LargeTitleHeader title="Kitchen Sink" /><SegmentedControl values={['Today','Tomorrow']} value={selected} onChange={setSelected} /><WidgetGrid><GlassSurface><h2>Weather</h2><p>26° • Partly cloudy</p></GlassSurface><GlassSurface><h2>Air quality</h2><p>Good</p></GlassSurface></WidgetGrid><ListGroup><ListRow label="Dark appearance"><Toggle checked={enabled} onChange={setEnabled} /></ListRow></ListGroup><p><PressableButton onClick={() => document.documentElement.classList.toggle('dark')}>Toggle theme</PressableButton></p><Link to="/">Back</Link></main> }
export default function App() { return <Routes><Route path="/" element={<Home />} /><Route path="/kitchen-sink" element={<KitchenSink />} /><Route path="*" element={<Home />} /></Routes> }
