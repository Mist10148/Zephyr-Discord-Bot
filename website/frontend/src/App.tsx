import { Route, Routes, useLocation } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { CommandPalette } from './components/CommandPalette'
import { RequireAuth } from './components/RequireAuth'
import { GuildAudit } from './routes/GuildAudit'
import { GuildMusic } from './routes/GuildMusic'
import { GuildOverview } from './routes/GuildOverview'
import { GuildSettings } from './routes/GuildSettings'
import { GuildWeatherAlerts } from './routes/GuildWeatherAlerts'
import { GuildAI } from './routes/GuildAI'
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
  return <AppShell><Routes><Route path="/" element={<Home />} /><Route path="/weather" element={<Weather />} /><Route path="/kitchen-sink" element={<KitchenSink />} /><Route path="/login" element={<Login />} /><Route path="/g" element={<RequireAuth><Guilds /></RequireAuth>} /><Route path="/g/:guildId" element={<RequireAuth><GuildOverview /></RequireAuth>} /><Route path="/g/:guildId/music" element={<RequireAuth><GuildMusic /></RequireAuth>} /><Route path="/g/:guildId/weather-alerts" element={<RequireAuth><GuildWeatherAlerts /></RequireAuth>} /><Route path="/g/:guildId/ai" element={<RequireAuth><GuildAI /></RequireAuth>} /><Route path="/g/:guildId/settings" element={<RequireAuth><GuildSettings /></RequireAuth>} /><Route path="/g/:guildId/audit" element={<RequireAuth><GuildAudit /></RequireAuth>} /><Route path="*" element={<NotFound />} /></Routes>{pathname !== '/login' && <CommandPalette />}</AppShell>
}
