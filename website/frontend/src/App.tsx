import { Route, Routes, useLocation } from 'react-router-dom'
import { CommandPalette } from './components/CommandPalette'
import { RequireAuth } from './components/RequireAuth'
import { Guilds } from './routes/Guilds'
import { Home } from './routes/Home'
import { KitchenSink } from './routes/KitchenSink'
import { Login } from './routes/Login'
import { NotFound } from './routes/NotFound'
import { Weather } from './routes/Weather'

export default function App() {
  const { pathname } = useLocation()
  // The palette is mounted beside <Routes>, so it otherwise appears on the
  // signed-out screen and fetches /commands there for nothing.
  return <><Routes><Route path="/" element={<Home />} /><Route path="/weather" element={<Weather />} /><Route path="/kitchen-sink" element={<KitchenSink />} /><Route path="/login" element={<Login />} /><Route path="/g" element={<RequireAuth><Guilds /></RequireAuth>} /><Route path="*" element={<NotFound />} /></Routes>{pathname !== '/login' && <CommandPalette />}</>
}
